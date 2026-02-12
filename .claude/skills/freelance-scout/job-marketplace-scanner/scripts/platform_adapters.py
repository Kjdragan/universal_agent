"""
Platform adapters for freelance marketplace scanning.

Each adapter normalizes platform-specific data into the unified Opportunity model.
Adapters are designed to be swapped, extended, or replaced independently.

Access Strategy Per Platform:
- Upwork: Official GraphQL API (OAuth 2.0) + web search fallback
- Freelancer.com: Official REST API (OAuth 2.0)  
- Fiverr: Web scraping / Apify actors (no official buyer-side API)
- Generic: Configurable web scraping for additional platforms

IMPORTANT: All adapters require platform credentials configured in environment
variables or a credentials file. See each adapter's docstring for requirements.
"""

import os
import json
import logging
import time
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote_plus, urlencode

logger = logging.getLogger("freelance_scout.adapters")


# Import models from sibling location
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import (
    Opportunity, ScanResult, Platform, ProjectType, ExperienceLevel,
    BudgetInfo, ClientInfo, OpportunityStatus
)


class PlatformAdapter(ABC):
    """
    Abstract base class for marketplace platform adapters.

    Each adapter must implement:
    - search(): Execute a search query and return normalized results
    - get_details(): Fetch full details for a specific opportunity
    - health_check(): Verify credentials and connectivity
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.rate_limit_remaining = None
        self.last_request_time = 0
        self.min_request_interval = 1.0  # seconds between requests

    @abstractmethod
    async def search(self, query: str, filters: dict = None, page: int = 0, 
                     page_size: int = 20) -> ScanResult:
        """Search for opportunities matching query and filters."""
        pass

    @abstractmethod
    async def get_details(self, opportunity_id: str) -> Optional[Opportunity]:
        """Fetch full details for a specific opportunity."""
        pass

    @abstractmethod
    async def health_check(self) -> dict:
        """Verify API credentials and connectivity. Returns status dict."""
        pass

    def _rate_limit(self):
        """Simple rate limiter - enforce minimum interval between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()


class UpworkAdapter(PlatformAdapter):
    """
    Upwork marketplace adapter using the official GraphQL API.

    Environment Variables Required:
        UPWORK_CLIENT_ID: OAuth 2.0 client ID from developer portal
        UPWORK_CLIENT_SECRET: OAuth 2.0 client secret
        UPWORK_ACCESS_TOKEN: OAuth 2.0 access token (after auth flow)
        UPWORK_TENANT_ID: Organization/tenant ID (X-Upwork-API-TenantId header)

    Setup Steps:
        1. Create account at https://www.upwork.com
        2. Request API key at https://www.upwork.com/developer/keys/
        3. Select 'Client Credentials' key type
        4. Select scopes: 'Marketplace Job Search' at minimum
        5. Complete OAuth 2.0 flow to obtain access token

    GraphQL Endpoint: https://api.upwork.com/graphql
    Rate Limits: Varies by scope, typically 100 req/min

    NOTE: As of late 2024, Upwork's GraphQL API has limited job search
    capabilities compared to the deprecated REST API. The marketplaceJobSearch
    query requires specific scopes. If GraphQL search is unavailable, the
    adapter falls back to web-based search URL construction for manual review.
    """

    GRAPHQL_ENDPOINT = "https://api.upwork.com/graphql"
    SEARCH_BASE_URL = "https://www.upwork.com/nx/search/jobs/"

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.client_id = os.getenv("UPWORK_CLIENT_ID", "")
        self.client_secret = os.getenv("UPWORK_CLIENT_SECRET", "")
        self.access_token = os.getenv("UPWORK_ACCESS_TOKEN", "")
        self.tenant_id = os.getenv("UPWORK_TENANT_ID", "")
        self.min_request_interval = 0.6  # ~100 req/min

    def _headers(self) -> dict:
        """Construct API request headers."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }
        if self.tenant_id:
            headers["X-Upwork-API-TenantId"] = self.tenant_id
        return headers

    async def search(self, query: str, filters: dict = None, page: int = 0,
                     page_size: int = 20) -> ScanResult:
        """
        Search Upwork marketplace for job opportunities.

        Attempts GraphQL API first; falls back to constructing search URLs
        for manual/web-scraping-based access if API is unavailable.

        Args:
            query: Search keywords (e.g., "python data analysis", "web scraping")
            filters: Optional filters dict with keys:
                - category: str (e.g., "Data Science & Analytics")
                - experience_level: str ("entry", "intermediate", "expert")
                - project_type: str ("fixed", "hourly")
                - budget_min: float
                - budget_max: float
                - client_hires_min: int (minimum client hire count)
                - posted_within: str ("24h", "3d", "7d", "14d", "30d")
                - sort: str ("recency", "relevance", "client_rating")
            page: Page number (0-indexed)
            page_size: Results per page (max 50)

        Returns:
            ScanResult with normalized Opportunity objects
        """
        self._rate_limit()
        filters = filters or {}
        result = ScanResult(
            platform=Platform.UPWORK,
            query=query,
        )

        # Try GraphQL API first
        if self.access_token:
            try:
                api_result = await self._search_graphql(query, filters, page, page_size)
                if api_result.opportunities:
                    return api_result
                # If empty, might be scope issue - fall through to URL method
                logger.warning("GraphQL search returned empty; falling back to URL construction")
            except Exception as e:
                logger.warning(f"GraphQL search failed: {e}; falling back to URL construction")
                result.errors.append(f"GraphQL API error: {str(e)}")

        # Fallback: Construct search URLs for web-based access
        search_url = self._build_search_url(query, filters, page)
        result.metadata['search_url'] = search_url
        result.metadata['access_method'] = 'url_construction'
        result.metadata['note'] = (
            "GraphQL API unavailable or returned empty. Use the search URL "
            "for web-based scraping or manual review. Consider using Apify "
            "Upwork scraper actors for automated extraction."
        )

        return result

    async def _search_graphql(self, query: str, filters: dict, page: int,
                               page_size: int) -> ScanResult:
        """Execute search via Upwork's GraphQL API."""
        import httpx

        # Construct GraphQL query
        graphql_query = """
        query marketplaceJobPostings($searchExpression: String!, $paging: MarketplaceJobPostingSearchPagingInput) {
            marketplaceJobPostingSearch(searchExpression: $searchExpression, paging: $paging) {
                totalCount
                edges {
                    node {
                        id
                        content {
                            title
                            description
                        }
                        classification {
                            category {
                                prefLabel
                            }
                            subcategory {
                                prefLabel
                            }
                            skills {
                                prefLabel
                            }
                        }
                        ownership {
                            team {
                                name
                                stats {
                                    totalJobsPosted
                                    totalSpent {
                                        rawValue
                                    }
                                    totalHires
                                }
                                location {
                                    country
                                    city
                                }
                                paymentVerificationStatus
                            }
                        }
                        budget {
                            amount {
                                rawValue
                            }
                            currencyCode
                            type
                        }
                        contractTerms {
                            contractType
                            experienceLevel
                            estimatedDuration {
                                label
                            }
                        }
                        activityStat {
                            totalApplicants
                            totalInvitedToInterview
                        }
                        publishedDateTime
                    }
                }
            }
        }
        """

        variables = {
            "searchExpression": query,
            "paging": {
                "offset": page * page_size,
                "count": min(page_size, 50)
            }
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.GRAPHQL_ENDPOINT,
                headers=self._headers(),
                json={"query": graphql_query, "variables": variables}
            )
            response.raise_for_status()
            data = response.json()

        # Check for GraphQL errors
        if 'errors' in data:
            error_msgs = [e.get('message', 'Unknown error') for e in data['errors']]
            raise Exception(f"GraphQL errors: {'; '.join(error_msgs)}")

        # Parse results into Opportunity objects
        result = ScanResult(platform=Platform.UPWORK, query=query)
        search_data = data.get('data', {}).get('marketplaceJobPostingSearch', {})
        result.total_results = search_data.get('totalCount', 0)

        for edge in search_data.get('edges', []):
            node = edge.get('node', {})
            opp = self._parse_graphql_node(node)
            result.opportunities.append(opp)

        result.metadata['access_method'] = 'graphql_api'
        return result

    def _parse_graphql_node(self, node: dict) -> Opportunity:
        """Parse a GraphQL job posting node into an Opportunity."""
        content = node.get('content', {})
        classification = node.get('classification', {})
        ownership = node.get('ownership', {})
        team = ownership.get('team', {})
        team_stats = team.get('stats', {})
        budget_data = node.get('budget', {})
        contract = node.get('contractTerms', {})
        activity = node.get('activityStat', {})

        # Parse budget
        budget = BudgetInfo()
        contract_type = contract.get('contractType', '')
        if contract_type == 'FIXED':
            budget.project_type = ProjectType.FIXED_PRICE
            amount = budget_data.get('amount', {}).get('rawValue')
            if amount:
                budget.fixed_price = float(amount)
        elif contract_type == 'HOURLY':
            budget.project_type = ProjectType.HOURLY

        duration = contract.get('estimatedDuration', {})
        if duration:
            budget.estimated_duration = duration.get('label', '')

        # Parse experience level
        exp_map = {
            'ENTRY': ExperienceLevel.ENTRY,
            'INTERMEDIATE': ExperienceLevel.INTERMEDIATE,
            'EXPERT': ExperienceLevel.EXPERT,
        }
        exp_level = exp_map.get(contract.get('experienceLevel', ''), ExperienceLevel.UNKNOWN)

        # Parse client info
        client = ClientInfo(
            country=team.get('location', {}).get('country'),
            city=team.get('location', {}).get('city'),
            total_spent=team_stats.get('totalSpent', {}).get('rawValue'),
            total_jobs_posted=team_stats.get('totalJobsPosted'),
            payment_verified=team.get('paymentVerificationStatus') == 'VERIFIED',
        )
        if client.total_jobs_posted and team_stats.get('totalHires'):
            client.hire_rate = team_stats['totalHires'] / max(client.total_jobs_posted, 1)

        # Parse skills
        skills = [s.get('prefLabel', '') for s in classification.get('skills', []) if s.get('prefLabel')]

        job_id = node.get('id', '')
        return Opportunity(
            id=job_id,
            platform=Platform.UPWORK,
            url=f"https://www.upwork.com/jobs/{job_id}" if job_id else "",
            title=content.get('title', ''),
            description=content.get('description', ''),
            category=classification.get('category', {}).get('prefLabel', ''),
            subcategory=classification.get('subcategory', {}).get('prefLabel', ''),
            skills_required=skills,
            experience_level=exp_level,
            budget=budget,
            client=client,
            proposals_count=activity.get('totalApplicants'),
            interviewing_count=activity.get('totalInvitedToInterview'),
            posted_at=node.get('publishedDateTime'),
        )

    def _build_search_url(self, query: str, filters: dict, page: int) -> str:
        """
        Construct an Upwork search URL with parameters.
        Useful as fallback when API is unavailable, or for directing
        web scraping tools to the right page.
        """
        params = {"q": query, "sort": "recency"}

        # Map filters to Upwork URL parameters
        if filters.get('project_type') == 'fixed':
            params['t'] = '0'
        elif filters.get('project_type') == 'hourly':
            params['t'] = '1'

        if filters.get('experience_level') == 'entry':
            params['contractor_tier'] = '1'
        elif filters.get('experience_level') == 'intermediate':
            params['contractor_tier'] = '2'
        elif filters.get('experience_level') == 'expert':
            params['contractor_tier'] = '3'

        posted_map = {
            '24h': '1', '3d': '3', '7d': '7', '14d': '14', '30d': '30'
        }
        if filters.get('posted_within') in posted_map:
            params['amount'] = posted_map[filters['posted_within']]

        if filters.get('budget_min'):
            params['min'] = str(int(filters['budget_min']))
        if filters.get('budget_max'):
            params['max'] = str(int(filters['budget_max']))

        if page > 0:
            params['page'] = str(page + 1)

        return f"{self.SEARCH_BASE_URL}?{urlencode(params)}"

    async def get_details(self, opportunity_id: str) -> Optional[Opportunity]:
        """Fetch full details for a specific Upwork job posting."""
        if not self.access_token:
            return None

        import httpx

        self._rate_limit()
        query = """
        query marketplaceJobPosting($id: ID!) {
            marketplaceJobPosting(id: $id) {
                id
                content { title description }
                classification {
                    category { prefLabel }
                    subcategory { prefLabel }
                    skills { prefLabel }
                }
                ownership {
                    team {
                        name
                        stats { totalJobsPosted totalSpent { rawValue } totalHires }
                        location { country city }
                        paymentVerificationStatus
                    }
                }
                budget { amount { rawValue } currencyCode type }
                contractTerms {
                    contractType
                    experienceLevel
                    estimatedDuration { label }
                }
                activityStat { totalApplicants totalInvitedToInterview }
                publishedDateTime
            }
        }
        """

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.GRAPHQL_ENDPOINT,
                headers=self._headers(),
                json={"query": query, "variables": {"id": opportunity_id}}
            )
            response.raise_for_status()
            data = response.json()

        if 'errors' in data:
            logger.error(f"GraphQL error fetching job {opportunity_id}: {data['errors']}")
            return None

        node = data.get('data', {}).get('marketplaceJobPosting')
        if not node:
            return None

        return self._parse_graphql_node(node)

    async def health_check(self) -> dict:
        """Verify Upwork API connectivity and credentials."""
        status = {
            'platform': 'upwork',
            'api_configured': bool(self.access_token),
            'graphql_available': False,
            'url_fallback': True,
            'errors': [],
        }

        if self.access_token:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    # Simple introspection query to verify connectivity
                    response = await client.post(
                        self.GRAPHQL_ENDPOINT,
                        headers=self._headers(),
                        json={"query": "{ __typename }"}
                    )
                    if response.status_code == 200:
                        status['graphql_available'] = True
                    else:
                        status['errors'].append(f"API returned status {response.status_code}")
            except Exception as e:
                status['errors'].append(str(e))

        return status


class FreelancerComAdapter(PlatformAdapter):
    """
    Freelancer.com marketplace adapter using the official REST API.

    Environment Variables Required:
        FREELANCER_OAUTH_TOKEN: OAuth 2.0 token from developer portal
        FREELANCER_SANDBOX: Set to "true" to use sandbox environment

    Setup Steps:
        1. Create account at https://www.freelancer.com
        2. Visit https://developers.freelancer.com
        3. Request API access / create OAuth application
        4. Complete OAuth 2.0 flow to obtain access token

    API Base: https://www.freelancer.com/api/projects/0.1/
    Python SDK: pip install freelancersdk

    This is the most API-friendly platform - full project search is supported
    with extensive filtering capabilities.
    """

    API_BASE = "https://www.freelancer.com/api"
    SANDBOX_BASE = "https://www.freelancer-sandbox.com/api"

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.oauth_token = os.getenv("FREELANCER_OAUTH_TOKEN", "")
        self.use_sandbox = os.getenv("FREELANCER_SANDBOX", "false").lower() == "true"
        self.base_url = self.SANDBOX_BASE if self.use_sandbox else self.API_BASE
        self.min_request_interval = 1.0

    def _headers(self) -> dict:
        return {
            "Freelancer-OAuth-V1": self.oauth_token,
            "Content-Type": "application/json",
        }

    async def search(self, query: str, filters: dict = None, page: int = 0,
                     page_size: int = 20) -> ScanResult:
        """
        Search Freelancer.com projects.

        Args:
            query: Search keywords
            filters: Optional filters dict with keys:
                - min_budget: float
                - max_budget: float
                - project_types: list[str] ("fixed", "hourly")
                - skills: list[int] (skill IDs)
                - countries: list[str]
                - sort: str ("time_updated", "price", "bid_count")
                - languages: list[str]
                - urgency: bool (urgent projects only)
            page: Page offset
            page_size: Results per page (max 100)
        """
        self._rate_limit()
        filters = filters or {}
        result = ScanResult(platform=Platform.FREELANCER, query=query)

        if not self.oauth_token:
            result.errors.append("FREELANCER_OAUTH_TOKEN not configured")
            result.metadata['search_url'] = self._build_search_url(query, filters)
            result.metadata['access_method'] = 'url_construction'
            return result

        try:
            import httpx

            # Build query parameters
            params = {
                "query": query,
                "limit": min(page_size, 100),
                "offset": page * page_size,
                "compact": "false",
                "job_details": "true",
                "user_details": "true",
                "project_details": "full",
            }

            if filters.get('min_budget'):
                params['min_avg_price'] = filters['min_budget']
            if filters.get('max_budget'):
                params['max_avg_price'] = filters['max_budget']
            if filters.get('sort'):
                sort_map = {
                    'newest': 'time_updated',
                    'price': 'price',
                    'bids': 'bid_count',
                }
                params['sort_field'] = sort_map.get(filters['sort'], 'time_updated')

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.base_url}/projects/0.1/projects/active/",
                    headers=self._headers(),
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            # Parse results
            api_result = data.get('result', {})
            projects = api_result.get('projects', [])
            result.total_results = api_result.get('total_count', len(projects))

            for project in projects:
                opp = self._parse_project(project)
                result.opportunities.append(opp)

            result.metadata['access_method'] = 'rest_api'

        except Exception as e:
            logger.error(f"Freelancer.com API error: {e}")
            result.errors.append(str(e))
            result.metadata['search_url'] = self._build_search_url(query, filters)
            result.metadata['access_method'] = 'url_construction'

        return result

    def _parse_project(self, project: dict) -> Opportunity:
        """Parse a Freelancer.com project into an Opportunity."""
        # Budget
        budget = BudgetInfo()
        proj_type = project.get('type', '')
        if proj_type == 'fixed':
            budget.project_type = ProjectType.FIXED_PRICE
            budget.min_budget = project.get('budget', {}).get('minimum')
            budget.max_budget = project.get('budget', {}).get('maximum')
        elif proj_type == 'hourly':
            budget.project_type = ProjectType.HOURLY
            budget.hourly_rate_min = project.get('hourly_project_info', {}).get('budget', {}).get('minimum')
            budget.hourly_rate_max = project.get('hourly_project_info', {}).get('budget', {}).get('maximum')

        budget.currency = project.get('currency', {}).get('code', 'USD')

        # Client
        owner = project.get('owner', {})
        client = ClientInfo(
            name=owner.get('username'),
            country=owner.get('location', {}).get('country', {}).get('name'),
            city=owner.get('location', {}).get('city'),
            rating=owner.get('employer_reputation', {}).get('overall'),
            payment_verified=owner.get('status', {}).get('payment_verified'),
        )

        # Skills/jobs
        jobs = project.get('jobs', [])
        skills = [j.get('name', '') for j in jobs if j.get('name')]

        proj_id = str(project.get('id', ''))
        return Opportunity(
            id=proj_id,
            platform=Platform.FREELANCER,
            url=f"https://www.freelancer.com/projects/{project.get('seo_url', proj_id)}" if proj_id else "",
            title=project.get('title', ''),
            description=project.get('description', '') or project.get('preview_description', ''),
            skills_required=skills,
            budget=budget,
            client=client,
            proposals_count=project.get('bid_stats', {}).get('bid_count'),
            posted_at=datetime.fromtimestamp(project.get('time_submitted', 0)).isoformat()
            if project.get('time_submitted') else None,
        )

    def _build_search_url(self, query: str, filters: dict) -> str:
        """Construct a Freelancer.com search URL for fallback."""
        params = {"q": query}
        return f"https://www.freelancer.com/jobs/{quote_plus(query)}/"

    async def get_details(self, opportunity_id: str) -> Optional[Opportunity]:
        """Fetch full project details from Freelancer.com."""
        if not self.oauth_token:
            return None

        import httpx
        self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.base_url}/projects/0.1/projects/{opportunity_id}/",
                    headers=self._headers(),
                    params={"full_description": "true", "user_details": "true"},
                )
                response.raise_for_status()
                data = response.json()

            project = data.get('result', {})
            if project:
                return self._parse_project(project)

        except Exception as e:
            logger.error(f"Error fetching project {opportunity_id}: {e}")

        return None

    async def health_check(self) -> dict:
        status = {
            'platform': 'freelancer_com',
            'api_configured': bool(self.oauth_token),
            'api_available': False,
            'sandbox': self.use_sandbox,
            'errors': [],
        }

        if self.oauth_token:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        f"{self.base_url}/users/0.1/self/",
                        headers=self._headers(),
                    )
                    if response.status_code == 200:
                        status['api_available'] = True
                        user_data = response.json().get('result', {})
                        status['authenticated_as'] = user_data.get('username')
                    else:
                        status['errors'].append(f"API returned status {response.status_code}")
            except Exception as e:
                status['errors'].append(str(e))

        return status


class WebScraperAdapter(PlatformAdapter):
    """
    Generic web scraper adapter for platforms without official APIs.

    This adapter uses configurable scraping strategies:
    1. Apify actors (recommended - handles anti-bot measures)
    2. Direct HTTP with BeautifulSoup (fragile but free)
    3. Playwright/Selenium (most robust but heaviest)

    Environment Variables:
        APIFY_API_TOKEN: Token for Apify cloud actors (recommended)
        SCRAPER_STRATEGY: "apify" | "http" | "browser" (default: "apify")

    Apify Actors Used:
        - jupri/upwork: Upwork job scraper
        - automation-lab/fiverr-scraper: Fiverr gig/listing scraper
        - hello.datawizards/freelancer-jobs-search-actor: Freelancer.com scraper

    NOTE: Web scraping may violate platform ToS. Use official APIs where available.
    This adapter exists as a research/intelligence gathering tool.
    """

    APIFY_BASE = "https://api.apify.com/v2"

    # Known Apify actor IDs for freelance platforms
    APIFY_ACTORS = {
        'upwork': 'jupri/upwork',
        'fiverr': 'automation-lab/fiverr-scraper',
        'freelancer': 'hello.datawizards/freelancer-jobs-search-actor',
    }

    def __init__(self, platform_name: str = "generic", config: dict = None):
        super().__init__(config)
        self.platform_name = platform_name
        self.apify_token = os.getenv("APIFY_API_TOKEN", "")
        self.strategy = os.getenv("SCRAPER_STRATEGY", "apify")
        self.min_request_interval = 5.0  # Be respectful with scraping

    async def search(self, query: str, filters: dict = None, page: int = 0,
                     page_size: int = 20) -> ScanResult:
        """
        Search using configured scraping strategy.
        
        For Apify strategy, runs the appropriate actor and waits for results.
        For HTTP strategy, makes direct requests with parsing.
        """
        filters = filters or {}
        result = ScanResult(
            platform=Platform(self.platform_name) if self.platform_name in [p.value for p in Platform] else Platform.CUSTOM,
            query=query,
        )

        if self.strategy == "apify" and self.apify_token:
            try:
                return await self._search_apify(query, filters, page_size)
            except Exception as e:
                logger.error(f"Apify search failed: {e}")
                result.errors.append(f"Apify error: {str(e)}")
        else:
            result.metadata['note'] = (
                f"No scraping strategy available for {self.platform_name}. "
                f"Configure APIFY_API_TOKEN for Apify-based scraping, or "
                f"use the official API adapter if available."
            )
            result.errors.append(f"No scraping strategy configured")

        return result

    async def _search_apify(self, query: str, filters: dict, 
                             page_size: int) -> ScanResult:
        """Run an Apify actor for scraping."""
        import httpx

        actor_id = self.APIFY_ACTORS.get(self.platform_name)
        if not actor_id:
            raise ValueError(f"No Apify actor configured for {self.platform_name}")

        # Actor-specific input construction
        if self.platform_name == 'upwork':
            actor_input = {
                "searchQuery": query,
                "maxResults": page_size,
            }
        elif self.platform_name == 'fiverr':
            actor_input = {
                "searchUrl": f"https://www.fiverr.com/search/gigs?query={quote_plus(query)}",
                "maxResults": page_size,
            }
        else:
            actor_input = {
                "query": query,
                "maxResults": page_size,
            }

        # Start actor run
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items",
                params={"token": self.apify_token},
                json=actor_input,
                timeout=120,
            )
            response.raise_for_status()
            items = response.json()

        # Parse results (format varies by actor)
        result = ScanResult(
            platform=Platform(self.platform_name) if self.platform_name in [p.value for p in Platform] else Platform.CUSTOM,
            query=query,
            total_results=len(items),
        )

        for item in items:
            opp = self._parse_scraped_item(item)
            if opp:
                result.opportunities.append(opp)

        result.metadata['access_method'] = 'apify_scraper'
        result.metadata['actor_id'] = actor_id
        return result

    def _parse_scraped_item(self, item: dict) -> Optional[Opportunity]:
        """
        Parse a scraped item into an Opportunity.
        Format varies by platform/actor - this is a best-effort normalizer.
        """
        title = (
            item.get('title') or 
            item.get('jobTitle') or 
            item.get('name') or
            item.get('gig_title') or
            ''
        )
        if not title:
            return None

        description = (
            item.get('description') or
            item.get('jobDescription') or
            item.get('preview_description') or
            ''
        )

        url = item.get('url') or item.get('link') or item.get('jobUrl') or ''

        # Try to extract budget info
        budget = BudgetInfo()
        price = item.get('budget') or item.get('price') or item.get('rate')
        if isinstance(price, dict):
            budget.min_budget = price.get('min') or price.get('minimum')
            budget.max_budget = price.get('max') or price.get('maximum')
        elif isinstance(price, (int, float)):
            budget.fixed_price = float(price)

        # Skills
        skills = item.get('skills') or item.get('tags') or item.get('required_skills') or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(',')]

        opp_id = str(item.get('id') or item.get('jobId') or hashlib.md5(title.encode()).hexdigest()[:12])

        return Opportunity(
            id=opp_id,
            platform=Platform(self.platform_name) if self.platform_name in [p.value for p in Platform] else Platform.CUSTOM,
            url=url,
            title=title,
            description=description,
            skills_required=skills if isinstance(skills, list) else [],
            budget=budget,
            proposals_count=item.get('proposals') or item.get('bid_count') or item.get('bids'),
            posted_at=item.get('publishedOn') or item.get('time_submitted') or item.get('postedOn'),
        )

    async def get_details(self, opportunity_id: str) -> Optional[Opportunity]:
        """Not implemented for generic scraper - would need URL-specific logic."""
        return None

    async def health_check(self) -> dict:
        return {
            'platform': self.platform_name,
            'strategy': self.strategy,
            'apify_configured': bool(self.apify_token),
            'actor_available': self.platform_name in self.APIFY_ACTORS,
        }


# ============================================================================
# Adapter Registry - Factory pattern for creating adapters
# ============================================================================

ADAPTER_REGISTRY = {
    'upwork': UpworkAdapter,
    'freelancer_com': FreelancerComAdapter,
    'fiverr': lambda config=None: WebScraperAdapter('fiverr', config),
}


def get_adapter(platform: str, config: dict = None) -> PlatformAdapter:
    """Factory function to get a platform adapter by name."""
    if platform not in ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown platform: {platform}. "
            f"Available: {list(ADAPTER_REGISTRY.keys())}"
        )
    creator = ADAPTER_REGISTRY[platform]
    if callable(creator) and not isinstance(creator, type):
        return creator(config)
    return creator(config)


def get_all_adapters(config: dict = None) -> dict[str, PlatformAdapter]:
    """Get all configured platform adapters."""
    adapters = {}
    for name in ADAPTER_REGISTRY:
        try:
            adapters[name] = get_adapter(name, config)
        except Exception as e:
            logger.warning(f"Failed to create adapter for {name}: {e}")
    return adapters
