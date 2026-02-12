"""
Unified data models for freelance marketplace opportunities.

These models normalize data from multiple platforms (Upwork, Freelancer.com, etc.)
into a common schema that the opportunity analyzer can score and rank.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import json
import hashlib


class Platform(Enum):
    UPWORK = "upwork"
    FREELANCER = "freelancer_com"
    FIVERR = "fiverr"
    GURU = "guru"
    PEOPLEPERHOUR = "peopleperhour"
    CUSTOM = "custom"


class ProjectType(Enum):
    FIXED_PRICE = "fixed_price"
    HOURLY = "hourly"
    UNKNOWN = "unknown"


class ExperienceLevel(Enum):
    ENTRY = "entry"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"
    UNKNOWN = "unknown"


class OpportunityStatus(Enum):
    NEW = "new"                      # Just discovered
    ANALYZED = "analyzed"            # Scored and assessed
    SHORTLISTED = "shortlisted"     # Meets criteria, candidate for bidding
    REJECTED = "rejected"           # Does not meet criteria
    EXPIRED = "expired"             # No longer available
    APPLIED = "applied"             # Bid submitted
    IN_PROGRESS = "in_progress"     # Contract active
    COMPLETED = "completed"         # Work delivered


@dataclass
class ClientInfo:
    """Normalized client/buyer information across platforms."""
    name: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    rating: Optional[float] = None              # 0.0 - 5.0 normalized
    total_spent: Optional[float] = None         # USD
    total_jobs_posted: Optional[int] = None
    hire_rate: Optional[float] = None           # 0.0 - 1.0
    member_since: Optional[str] = None          # ISO date string
    payment_verified: Optional[bool] = None
    reviews_count: Optional[int] = None


@dataclass
class BudgetInfo:
    """Normalized budget/pricing information."""
    project_type: ProjectType = ProjectType.UNKNOWN
    min_budget: Optional[float] = None          # USD
    max_budget: Optional[float] = None          # USD
    fixed_price: Optional[float] = None         # USD (for fixed-price projects)
    hourly_rate_min: Optional[float] = None     # USD/hr
    hourly_rate_max: Optional[float] = None     # USD/hr
    estimated_hours: Optional[int] = None
    estimated_duration: Optional[str] = None    # e.g., "1-3 months", "Less than a week"
    currency: str = "USD"

    @property
    def estimated_value(self) -> Optional[float]:
        """Best estimate of total project value in USD."""
        if self.fixed_price:
            return self.fixed_price
        if self.min_budget and self.max_budget:
            return (self.min_budget + self.max_budget) / 2
        if self.max_budget:
            return self.max_budget
        if self.min_budget:
            return self.min_budget
        if self.hourly_rate_max and self.estimated_hours:
            return self.hourly_rate_max * self.estimated_hours
        return None


@dataclass
class Opportunity:
    """
    Unified opportunity model representing a job/project from any platform.

    This is the core data object that flows through the entire pipeline:
    Scanner → Analyzer → Reporter → (eventually) Bidder
    """
    # Identity
    id: str = ""                                    # Platform-specific job ID
    platform: Platform = Platform.CUSTOM
    url: str = ""                                   # Direct link to the listing
    fingerprint: str = ""                           # Dedup hash

    # Content
    title: str = ""
    description: str = ""
    category: str = ""
    subcategory: str = ""
    skills_required: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Requirements
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    budget: BudgetInfo = field(default_factory=BudgetInfo)
    client: ClientInfo = field(default_factory=ClientInfo)

    # Competition
    proposals_count: Optional[int] = None           # Number of bids/proposals
    interviewing_count: Optional[int] = None
    invites_only: bool = False

    # Timestamps
    posted_at: Optional[str] = None                 # ISO datetime
    discovered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    expires_at: Optional[str] = None

    # Analysis results (populated by opportunity-analyzer)
    status: OpportunityStatus = OpportunityStatus.NEW
    feasibility_score: Optional[float] = None       # 0.0 - 1.0
    value_score: Optional[float] = None             # 0.0 - 1.0
    competition_score: Optional[float] = None       # 0.0 - 1.0 (higher = less competition)
    overall_score: Optional[float] = None           # 0.0 - 1.0 weighted composite
    required_capabilities: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    estimated_effort_hours: Optional[float] = None
    analysis_notes: str = ""
    rejection_reason: str = ""

    def __post_init__(self):
        """Generate fingerprint for deduplication if not set."""
        if not self.fingerprint and self.title and self.platform:
            raw = f"{self.platform.value}:{self.id}:{self.title}"
            self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Serialize to dictionary with enum handling."""
        d = asdict(self)
        # Convert enums to their values
        d['platform'] = self.platform.value
        d['budget']['project_type'] = self.budget.project_type.value
        d['experience_level'] = self.experience_level.value
        d['status'] = self.status.value
        return d

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "Opportunity":
        """Deserialize from dictionary."""
        # Handle nested objects
        if 'budget' in data and isinstance(data['budget'], dict):
            budget_data = data['budget']
            if 'project_type' in budget_data:
                budget_data['project_type'] = ProjectType(budget_data['project_type'])
            data['budget'] = BudgetInfo(**budget_data)

        if 'client' in data and isinstance(data['client'], dict):
            data['client'] = ClientInfo(**data['client'])

        # Handle enums
        if 'platform' in data and isinstance(data['platform'], str):
            data['platform'] = Platform(data['platform'])
        if 'experience_level' in data and isinstance(data['experience_level'], str):
            data['experience_level'] = ExperienceLevel(data['experience_level'])
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = OpportunityStatus(data['status'])

        return cls(**data)


@dataclass
class ScanResult:
    """Result of a marketplace scan operation."""
    platform: Platform
    query: str
    opportunities: list[Opportunity] = field(default_factory=list)
    total_results: int = 0
    scan_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    next_page_token: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            'platform': self.platform.value,
            'query': self.query,
            'opportunities': [o.to_dict() for o in self.opportunities],
            'total_results': self.total_results,
            'scan_timestamp': self.scan_timestamp,
            'next_page_token': self.next_page_token,
            'errors': self.errors,
            'metadata': self.metadata,
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


@dataclass
class DailyDigest:
    """Aggregated daily intelligence report."""
    date: str
    total_scanned: int = 0
    new_opportunities: int = 0
    shortlisted: list[Opportunity] = field(default_factory=list)
    top_categories: dict = field(default_factory=dict)       # category -> count
    top_skills_demanded: dict = field(default_factory=dict)  # skill -> count
    avg_budget_by_category: dict = field(default_factory=dict)
    platform_breakdown: dict = field(default_factory=dict)   # platform -> count
    trend_signals: list[str] = field(default_factory=list)
    capability_gaps: list[str] = field(default_factory=list) # skills we're missing
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'date': self.date,
            'total_scanned': self.total_scanned,
            'new_opportunities': self.new_opportunities,
            'shortlisted': [o.to_dict() for o in self.shortlisted],
            'top_categories': self.top_categories,
            'top_skills_demanded': self.top_skills_demanded,
            'avg_budget_by_category': self.avg_budget_by_category,
            'platform_breakdown': self.platform_breakdown,
            'trend_signals': self.trend_signals,
            'capability_gaps': self.capability_gaps,
            'recommendations': self.recommendations,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
