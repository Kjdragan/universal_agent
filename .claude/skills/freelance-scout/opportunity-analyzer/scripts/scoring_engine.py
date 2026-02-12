"""
Opportunity Scoring Engine

Scores and ranks freelance opportunities based on multiple weighted factors:
- Feasibility: Can we actually deliver this with our current capabilities?
- Value: Is the pay worth the effort?
- Competition: How many others are bidding? Can we differentiate?
- Client Quality: Is this client likely to pay, communicate well, and rehire?
- Strategic Fit: Does this align with our barbell strategy and growth goals?

The engine is designed to be calibrated over time as we learn which factors
actually predict successful engagements.
"""

import os
import sys
import json
import logging
import re
from datetime import datetime
from typing import Optional
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import (
    Opportunity, OpportunityStatus, ProjectType, ExperienceLevel,
    DailyDigest, BudgetInfo
)

logger = logging.getLogger("freelance_scout.scoring")


# ============================================================================
# Capability Registry - What can our system currently do?
# ============================================================================

# These represent our current UA system capabilities.
# As we build and improve skills, this registry grows.
# Each capability maps to a confidence level (0.0 - 1.0)
# and a list of skill tags that indicate this capability is needed.

DEFAULT_CAPABILITIES = {
    # Tier: STRONG (0.8-1.0) - Core competencies we can deliver reliably
    "python_development": {
        "confidence": 0.95,
        "keywords": ["python", "python3", "django", "flask", "fastapi", "pandas",
                      "numpy", "scipy", "matplotlib", "jupyter"],
    },
    "web_scraping": {
        "confidence": 0.90,
        "keywords": ["web scraping", "scraping", "beautifulsoup", "scrapy",
                      "selenium", "playwright", "crawling", "data extraction"],
    },
    "data_analysis": {
        "confidence": 0.85,
        "keywords": ["data analysis", "data analytics", "data science",
                      "statistical analysis", "data processing", "data cleaning",
                      "etl", "data pipeline"],
    },
    "api_development": {
        "confidence": 0.90,
        "keywords": ["api", "rest api", "graphql", "api integration",
                      "api development", "webhook", "microservices"],
    },
    "ai_ml": {
        "confidence": 0.80,
        "keywords": ["machine learning", "ai", "artificial intelligence",
                      "nlp", "natural language processing", "llm", "gpt",
                      "chatbot", "openai", "langchain", "hugging face",
                      "generative ai", "prompt engineering"],
    },
    "automation": {
        "confidence": 0.90,
        "keywords": ["automation", "workflow automation", "process automation",
                      "scripting", "task automation", "cron", "scheduling",
                      "n8n", "zapier", "make"],
    },
    "technical_writing": {
        "confidence": 0.85,
        "keywords": ["technical writing", "documentation", "api documentation",
                      "user guide", "technical documentation"],
    },
    "content_writing": {
        "confidence": 0.80,
        "keywords": ["content writing", "blog writing", "article writing",
                      "copywriting", "seo writing", "blog post"],
    },
    "research": {
        "confidence": 0.90,
        "keywords": ["research", "market research", "competitive analysis",
                      "data collection", "literature review", "report writing"],
    },
    "data_entry": {
        "confidence": 0.95,
        "keywords": ["data entry", "data processing", "spreadsheet",
                      "excel", "google sheets", "csv"],
    },

    # Tier: MODERATE (0.5-0.8) - Can do but might need more effort
    "javascript_development": {
        "confidence": 0.70,
        "keywords": ["javascript", "node.js", "nodejs", "react", "next.js",
                      "typescript", "vue", "angular", "express"],
    },
    "database": {
        "confidence": 0.75,
        "keywords": ["database", "sql", "postgresql", "mysql", "mongodb",
                      "nosql", "database design", "database management"],
    },
    "cloud_devops": {
        "confidence": 0.65,
        "keywords": ["aws", "gcp", "google cloud", "azure", "docker",
                      "kubernetes", "ci/cd", "devops", "terraform"],
    },
    "seo": {
        "confidence": 0.60,
        "keywords": ["seo", "search engine optimization", "keyword research",
                      "on-page seo", "seo audit"],
    },

    # Tier: WEAK (0.0-0.5) - Major gaps, shouldn't bid unless learning
    "mobile_development": {
        "confidence": 0.20,
        "keywords": ["mobile app", "ios", "android", "flutter", "react native",
                      "swift", "kotlin"],
    },
    "graphic_design": {
        "confidence": 0.30,
        "keywords": ["graphic design", "logo design", "photoshop", "illustrator",
                      "figma", "ui design", "ux design", "branding"],
    },
    "video_editing": {
        "confidence": 0.10,
        "keywords": ["video editing", "video production", "after effects",
                      "premiere pro", "animation", "motion graphics"],
    },
}


class OpportunityScoringEngine:
    """
    Multi-factor scoring engine for freelance opportunities.

    Produces a composite score (0.0 - 1.0) from weighted sub-scores:
    - feasibility_score: Can we do this?
    - value_score: Is it worth doing?
    - competition_score: Can we win the bid?
    - client_score: Is this a good client?
    - strategic_score: Does it fit our growth plan?
    """

    # Default scoring weights (should be tuned based on actual outcomes)
    DEFAULT_WEIGHTS = {
        'feasibility': 0.30,    # Most important - can we deliver?
        'value': 0.25,          # Second - is it worth it?
        'competition': 0.15,    # Third - can we win?
        'client': 0.15,         # Fourth - good client?
        'strategic': 0.15,      # Fifth - fits our plan?
    }

    # Strategic priority keywords (for barbell strategy)
    HIGH_VALUE_CATEGORIES = [
        "ai", "machine learning", "data science", "automation",
        "api integration", "consulting", "strategy",
    ]
    QUICK_WIN_CATEGORIES = [
        "data entry", "web scraping", "research", "spreadsheet",
        "content writing", "blog writing", "documentation",
    ]

    def __init__(self, capabilities: dict = None, weights: dict = None,
                 min_hourly_rate: float = 15.0, min_fixed_price: float = 50.0):
        self.capabilities = capabilities or DEFAULT_CAPABILITIES
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.min_hourly_rate = min_hourly_rate
        self.min_fixed_price = min_fixed_price

    def score_opportunity(self, opp: Opportunity) -> Opportunity:
        """
        Score an opportunity across all dimensions.
        Mutates the opportunity in-place and returns it.
        """
        # Compute individual scores
        opp.feasibility_score = self._score_feasibility(opp)
        opp.value_score = self._score_value(opp)
        opp.competition_score = self._score_competition(opp)

        client_score = self._score_client(opp)
        strategic_score = self._score_strategic(opp)

        # Weighted composite
        opp.overall_score = (
            self.weights['feasibility'] * opp.feasibility_score +
            self.weights['value'] * opp.value_score +
            self.weights['competition'] * opp.competition_score +
            self.weights['client'] * client_score +
            self.weights['strategic'] * strategic_score
        )

        # Determine status based on score
        if opp.feasibility_score < 0.2:
            opp.status = OpportunityStatus.REJECTED
            opp.rejection_reason = "Insufficient capabilities to deliver"
        elif opp.overall_score >= 0.6:
            opp.status = OpportunityStatus.SHORTLISTED
        elif opp.overall_score >= 0.4:
            opp.status = OpportunityStatus.ANALYZED
        else:
            opp.status = OpportunityStatus.REJECTED
            opp.rejection_reason = f"Score too low ({opp.overall_score:.2f})"

        # Generate analysis notes
        opp.analysis_notes = self._generate_analysis_notes(
            opp, client_score, strategic_score
        )

        return opp

    def _score_feasibility(self, opp: Opportunity) -> float:
        """
        Can we deliver this? Score based on skill match.

        Looks at required skills, description keywords, and category
        to determine how many of our capabilities apply.
        """
        # Combine all text signals
        all_text = " ".join([
            opp.title.lower(),
            opp.description.lower(),
            " ".join(opp.skills_required).lower(),
            opp.category.lower(),
            opp.subcategory.lower(),
        ])

        matched_capabilities = []
        missing_capabilities = []
        total_confidence = 0.0
        match_count = 0

        for cap_name, cap_info in self.capabilities.items():
            keywords = cap_info['keywords']
            # Check if any keyword matches
            matched = any(kw.lower() in all_text for kw in keywords)
            if matched:
                matched_capabilities.append(cap_name)
                total_confidence += cap_info['confidence']
                match_count += 1

        # Also check for skills we explicitly don't have
        for skill in opp.skills_required:
            skill_lower = skill.lower()
            found = False
            for cap_info in self.capabilities.values():
                if any(kw.lower() in skill_lower or skill_lower in kw.lower()
                       for kw in cap_info['keywords']):
                    found = True
                    break
            if not found:
                missing_capabilities.append(skill)

        opp.required_capabilities = matched_capabilities
        opp.missing_capabilities = missing_capabilities

        if match_count == 0:
            return 0.1  # Some base score - might still be doable

        # Average confidence of matched capabilities, penalized by missing ones
        avg_confidence = total_confidence / match_count
        missing_penalty = min(len(missing_capabilities) * 0.15, 0.5)

        return max(0.0, min(1.0, avg_confidence - missing_penalty))

    def _score_value(self, opp: Opportunity) -> float:
        """
        Is this worth our time? Score based on budget and effort ratio.
        """
        estimated_value = opp.budget.estimated_value

        if estimated_value is None:
            return 0.4  # Unknown budget - neutral score

        # For fixed-price projects
        if opp.budget.project_type == ProjectType.FIXED_PRICE:
            if estimated_value < self.min_fixed_price:
                return 0.1  # Too low
            elif estimated_value < 100:
                return 0.3
            elif estimated_value < 500:
                return 0.5
            elif estimated_value < 2000:
                return 0.7
            elif estimated_value < 10000:
                return 0.85
            else:
                return 0.95

        # For hourly projects
        elif opp.budget.project_type == ProjectType.HOURLY:
            rate = opp.budget.hourly_rate_max or opp.budget.hourly_rate_min or 0
            if rate < self.min_hourly_rate:
                return 0.1
            elif rate < 30:
                return 0.3
            elif rate < 50:
                return 0.5
            elif rate < 100:
                return 0.7
            elif rate < 200:
                return 0.85
            else:
                return 0.95

        # Budget present but type unknown
        if opp.budget.min_budget or opp.budget.max_budget:
            avg = (opp.budget.min_budget or 0 + opp.budget.max_budget or 0) / 2
            if avg < 50:
                return 0.2
            elif avg < 500:
                return 0.5
            elif avg < 5000:
                return 0.7
            else:
                return 0.9

        return 0.4

    def _score_competition(self, opp: Opportunity) -> float:
        """
        How competitive is this opportunity?
        Lower proposal count = higher score (better chance of winning).
        """
        if opp.invites_only:
            return 0.9  # Invite-only = low competition

        proposals = opp.proposals_count
        if proposals is None:
            return 0.5  # Unknown - neutral

        if proposals == 0:
            return 0.95  # No competition yet!
        elif proposals <= 5:
            return 0.85
        elif proposals <= 10:
            return 0.70
        elif proposals <= 20:
            return 0.50
        elif proposals <= 35:
            return 0.30
        elif proposals <= 50:
            return 0.15
        else:
            return 0.05  # Very crowded

    def _score_client(self, opp: Opportunity) -> float:
        """
        Is this a good client? Score based on history and verification.
        """
        client = opp.client
        score = 0.5  # Start neutral

        # Payment verification
        if client.payment_verified:
            score += 0.15
        elif client.payment_verified is False:
            score -= 0.15

        # Spending history
        if client.total_spent is not None:
            if client.total_spent > 100000:
                score += 0.15
            elif client.total_spent > 10000:
                score += 0.10
            elif client.total_spent > 1000:
                score += 0.05
            elif client.total_spent == 0:
                score -= 0.05  # New client, slight risk

        # Hire rate
        if client.hire_rate is not None:
            if client.hire_rate > 0.7:
                score += 0.10
            elif client.hire_rate < 0.2:
                score -= 0.10

        # Rating
        if client.rating is not None:
            if client.rating >= 4.5:
                score += 0.10
            elif client.rating >= 4.0:
                score += 0.05
            elif client.rating < 3.0:
                score -= 0.15

        return max(0.0, min(1.0, score))

    def _score_strategic(self, opp: Opportunity) -> float:
        """
        Does this fit our barbell strategy?

        High scores for:
        - Quick wins: Easy jobs that build reputation and cash flow
        - High value: Premium work in our strongest domains
        - Skill builders: Jobs that develop capabilities we're investing in
        """
        all_text = f"{opp.title} {opp.description} {' '.join(opp.skills_required)}".lower()
        score = 0.5  # Neutral baseline

        # Barbell: Quick win end (easy, reputation-building)
        quick_win_matches = sum(1 for kw in self.QUICK_WIN_CATEGORIES if kw in all_text)
        if quick_win_matches >= 2:
            score += 0.20
        elif quick_win_matches >= 1:
            score += 0.10

        # Barbell: High value end (premium, differentiating)
        high_value_matches = sum(1 for kw in self.HIGH_VALUE_CATEGORIES if kw in all_text)
        if high_value_matches >= 2:
            score += 0.25
        elif high_value_matches >= 1:
            score += 0.15

        # Penalize middle ground (neither easy nor premium)
        if quick_win_matches == 0 and high_value_matches == 0:
            score -= 0.10

        # Bonus: Repeat client potential (longer projects, ongoing work)
        duration = (opp.budget.estimated_duration or "").lower()
        if "ongoing" in duration or "month" in duration:
            score += 0.10
        elif "week" in duration:
            score += 0.05

        return max(0.0, min(1.0, score))

    def _generate_analysis_notes(self, opp: Opportunity, 
                                  client_score: float, 
                                  strategic_score: float) -> str:
        """Generate human-readable analysis summary."""
        notes = []

        # Feasibility assessment
        if opp.feasibility_score >= 0.8:
            notes.append(f"Strong capability match ({', '.join(opp.required_capabilities[:3])})")
        elif opp.feasibility_score >= 0.5:
            notes.append(f"Partial capability match. Gaps: {', '.join(opp.missing_capabilities[:3])}")
        else:
            notes.append(f"Weak match. Missing: {', '.join(opp.missing_capabilities[:5])}")

        # Value assessment
        est_val = opp.budget.estimated_value
        if est_val:
            notes.append(f"Est. value: ${est_val:,.0f}")

        # Competition
        if opp.proposals_count is not None:
            if opp.proposals_count <= 5:
                notes.append(f"Low competition ({opp.proposals_count} proposals)")
            elif opp.proposals_count >= 30:
                notes.append(f"High competition ({opp.proposals_count} proposals)")

        # Client quality
        if client_score >= 0.7:
            notes.append("Strong client profile")
        elif client_score <= 0.3:
            notes.append("Weak client profile - proceed with caution")

        # Strategic fit
        if strategic_score >= 0.7:
            notes.append("Excellent strategic fit (barbell strategy)")
        elif strategic_score <= 0.3:
            notes.append("Poor strategic fit")

        return " | ".join(notes)

    def score_batch(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        """Score a batch of opportunities and return sorted by overall_score."""
        scored = [self.score_opportunity(opp) for opp in opportunities]
        scored.sort(key=lambda o: o.overall_score or 0, reverse=True)
        return scored


# ============================================================================
# Digest Generator - Produces daily intelligence reports
# ============================================================================

class DigestGenerator:
    """
    Aggregates scored opportunities into a daily intelligence digest.

    The digest provides:
    - Top opportunities ranked by score
    - Category and skill demand trends
    - Capability gap analysis (what skills should we build?)
    - Strategic recommendations
    """

    def __init__(self, shortlist_threshold: float = 0.55, max_shortlist: int = 20):
        self.shortlist_threshold = shortlist_threshold
        self.max_shortlist = max_shortlist

    def generate(self, opportunities: list[Opportunity], 
                 date: str = None) -> DailyDigest:
        """Generate a digest from a batch of scored opportunities."""
        date = date or datetime.utcnow().strftime("%Y-%m-%d")

        digest = DailyDigest(
            date=date,
            total_scanned=len(opportunities),
        )

        # Count new opportunities (discovered today)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        digest.new_opportunities = sum(
            1 for o in opportunities
            if o.discovered_at and o.discovered_at.startswith(today)
        )

        # Shortlist top opportunities
        scored = [o for o in opportunities if o.overall_score is not None]
        scored.sort(key=lambda o: o.overall_score, reverse=True)
        digest.shortlisted = [
            o for o in scored
            if o.overall_score >= self.shortlist_threshold
        ][:self.max_shortlist]

        # Category analysis
        categories = Counter()
        skills = Counter()
        budget_sums = {}
        budget_counts = {}
        platform_counts = Counter()

        for opp in opportunities:
            if opp.category:
                categories[opp.category] += 1
                val = opp.budget.estimated_value
                if val:
                    budget_sums[opp.category] = budget_sums.get(opp.category, 0) + val
                    budget_counts[opp.category] = budget_counts.get(opp.category, 0) + 1

            for skill in opp.skills_required:
                skills[skill] += 1

            platform_counts[opp.platform.value] += 1

        digest.top_categories = dict(categories.most_common(15))
        digest.top_skills_demanded = dict(skills.most_common(25))
        digest.platform_breakdown = dict(platform_counts)

        # Average budget by category
        for cat in budget_sums:
            if budget_counts.get(cat, 0) > 0:
                digest.avg_budget_by_category[cat] = round(
                    budget_sums[cat] / budget_counts[cat], 2
                )

        # Capability gaps - skills frequently required but we're weak at
        all_missing = Counter()
        for opp in opportunities:
            for cap in opp.missing_capabilities:
                all_missing[cap] += 1
        digest.capability_gaps = [
            f"{skill} (needed by {count} jobs)"
            for skill, count in all_missing.most_common(10)
        ]

        # Strategic recommendations
        digest.recommendations = self._generate_recommendations(
            opportunities, digest
        )

        # Trend signals
        digest.trend_signals = self._detect_trends(opportunities, skills)

        return digest

    def _generate_recommendations(self, opportunities: list[Opportunity],
                                   digest: DailyDigest) -> list[str]:
        """Generate actionable recommendations based on the data."""
        recs = []

        # High-value shortlisted opportunities
        if digest.shortlisted:
            top = digest.shortlisted[0]
            recs.append(
                f"Top opportunity: \"{top.title}\" (score: {top.overall_score:.2f}, "
                f"est. value: ${top.budget.estimated_value or 'N/A'})"
            )

        # Capability gap recommendations
        if digest.capability_gaps:
            top_gap = digest.capability_gaps[0]
            recs.append(f"Priority skill to develop: {top_gap}")

        # Category concentration
        if digest.top_categories:
            top_cat = list(digest.top_categories.keys())[0]
            top_count = list(digest.top_categories.values())[0]
            recs.append(
                f"Most active category: {top_cat} ({top_count} jobs) - "
                f"consider deepening capabilities here"
            )

        # Competition analysis
        low_comp = [o for o in opportunities
                    if o.competition_score and o.competition_score > 0.7
                    and o.feasibility_score and o.feasibility_score > 0.5]
        if low_comp:
            recs.append(
                f"{len(low_comp)} low-competition opportunities with good feasibility found"
            )

        # Budget insights
        high_value = [o for o in opportunities
                      if o.budget.estimated_value and o.budget.estimated_value > 1000
                      and o.feasibility_score and o.feasibility_score > 0.5]
        if high_value:
            recs.append(
                f"{len(high_value)} opportunities over $1,000 within our capabilities"
            )

        return recs

    def _detect_trends(self, opportunities: list[Opportunity],
                       skills: Counter) -> list[str]:
        """Detect emerging trends from opportunity data."""
        signals = []

        # AI/ML demand signal
        ai_keywords = ['ai', 'machine learning', 'llm', 'gpt', 'openai',
                        'langchain', 'generative ai', 'chatbot']
        ai_count = sum(skills.get(kw, 0) for kw in ai_keywords)
        total = sum(skills.values()) or 1
        if ai_count / total > 0.15:
            signals.append(f"Strong AI/ML demand signal ({ai_count} mentions across opportunities)")

        # Automation demand
        auto_keywords = ['automation', 'workflow', 'n8n', 'zapier', 'make',
                          'automate', 'bot']
        auto_count = sum(skills.get(kw, 0) for kw in auto_keywords)
        if auto_count / total > 0.10:
            signals.append(f"Automation demand trending ({auto_count} mentions)")

        # Python dominance
        python_count = skills.get('python', 0) + skills.get('python3', 0)
        if python_count / total > 0.20:
            signals.append(f"Python strongly demanded ({python_count} mentions) - our core strength")

        return signals
