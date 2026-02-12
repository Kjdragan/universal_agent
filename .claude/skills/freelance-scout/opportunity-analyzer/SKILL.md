# Opportunity Analyzer

## Metadata
| Field | Value |
|-------|-------|
| **Name** | opportunity-analyzer |
| **Version** | 1.0.0 |
| **Tier** | 1 — Autonomous Freelancer Engine |
| **Priority** | P1 (Critical) |
| **Complexity** | High |
| **Agent Level** | Both (Sub-Agent deep analysis, Primary Agent quick filtering) |
| **Dependencies** | job-marketplace-scanner, capability-self-assessment |

## Description

Scores, ranks, and generates intelligence from discovered freelance opportunities. This is the "brain" that transforms raw job listings into actionable intelligence — determining which opportunities are worth pursuing, identifying skill gaps to address, and producing daily digests that drive strategic decisions.

The analyzer operates on the unified Opportunity data model and produces multi-dimensional scores across five factors: feasibility, value, competition, client quality, and strategic fit.

## Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| opportunities | list[Opportunity] | Yes | Raw or previously scored opportunities from scanner |
| capability_registry | dict | No | Override default capabilities (skill → confidence map) |
| scoring_weights | dict | No | Override default scoring weights |
| min_hourly_rate | float | No | Minimum acceptable hourly rate (default: $15) |
| min_fixed_price | float | No | Minimum acceptable fixed price (default: $50) |
| shortlist_threshold | float | No | Minimum overall_score to shortlist (default: 0.55) |

## Outputs

| Output | Type | Description |
|--------|------|-------------|
| scored_opportunities | list[Opportunity] | All opportunities with scores populated, sorted by overall_score |
| daily_digest | DailyDigest | Aggregated intelligence report |
| shortlist | list[Opportunity] | Top opportunities meeting threshold |
| capability_gaps | list[str] | Skills frequently required but missing from our registry |
| trend_signals | list[str] | Market trend observations |

## Instructions

### Step 1: Score Each Opportunity

The scoring engine evaluates five dimensions (weights configurable):

**Feasibility Score (30% weight)**
Matches opportunity requirements against our capability registry. Each capability has keywords and a confidence level (0.0–1.0). The score reflects average confidence of matched capabilities, penalized by the number of unmatched required skills.

**Value Score (25% weight)**
Evaluates budget relative to effort. Fixed-price projects scored on absolute value; hourly projects scored on rate. Scale:
- < $50 fixed / < $15/hr → 0.1 (too low)
- $500–$2,000 fixed / $50–$100/hr → 0.7 (good value)
- > $10,000 fixed / > $200/hr → 0.95 (premium)

**Competition Score (15% weight)**
Inverse of proposal/bid count. Fewer competing bids = higher score:
- 0 proposals → 0.95 (first mover advantage)
- 5–10 proposals → 0.70 (manageable)
- 35+ proposals → 0.15 (very crowded)
- Invite-only → 0.90

**Client Quality Score (15% weight)**
Composite of payment verification, spending history, hire rate, and ratings. Verified clients with $10K+ spend history and high ratings score highest.

**Strategic Fit Score (15% weight)**
Alignment with barbell strategy. High scores for:
- Quick wins: Easy jobs matching multiple "quick win" keywords
- Premium work: High-value jobs in differentiating domains (AI, ML, consulting)
- Ongoing potential: Projects with monthly/ongoing duration suggesting repeat business
Penalizes "middle ground" jobs that are neither easy wins nor premium.

### Step 2: Classify Opportunities

Based on composite score:
- **Shortlisted** (≥ 0.60): Actively pursue — add to bidding pipeline
- **Analyzed** (0.40–0.59): Worth monitoring — might improve with capability development
- **Rejected** (< 0.40): Skip — poor fit, low value, or too competitive

Special rejection: If feasibility_score < 0.20, reject regardless of other scores (can't deliver).

### Step 3: Generate Daily Digest

The digest aggregates scored opportunities into an intelligence report:

1. **Overview Stats**: Total scanned, new today, shortlisted count
2. **Top Opportunities**: Ranked list with scores, estimated values, and analysis notes
3. **Category Analysis**: Most active job categories and average budgets
4. **Skill Demand**: Most frequently required skills across all opportunities
5. **Capability Gaps**: Skills our system is missing that are frequently demanded
6. **Trend Signals**: Emerging patterns (AI demand surge, automation trending, etc.)
7. **Recommendations**: Actionable items (top opportunity to bid on, skills to develop, categories to focus on)

### Step 4: Calibrate Over Time

The scoring engine is designed for continuous calibration:

1. **Track Outcomes**: Record which scored opportunities we bid on and their outcomes (won/lost, client satisfaction, profitability)
2. **Adjust Weights**: If high-value scores don't predict profitable engagements, reduce value weight; if client quality better predicts success, increase its weight
3. **Update Capabilities**: As we build new skills, add them to the capability registry with honest confidence levels
4. **Tune Thresholds**: If shortlist is too long, raise threshold; if too short, lower it

## Sub-Agent Mode (Deep Analysis)

When operating as the freelance-scout's dedicated analyzer, perform deep analysis:
- Read the full job description carefully, not just title/skills
- Assess ambiguity (vague descriptions often indicate difficult clients)
- Check for red flags: unrealistic timelines, extremely low budgets, "need ASAP" with no payment verification
- Estimate effort in hours based on description complexity and skill requirements
- Generate a specific bid strategy recommendation for shortlisted items

## Primary Agent Mode (Quick Filter)

When called by the primary agent for rapid triage:
- Use only feasibility_score and value_score (skip client/strategic analysis)
- Return binary: pass/fail with one-line justification
- Suitable for filtering large batches before detailed review

## Capability Registry

The default capability registry represents our current system's honest self-assessment:

**Strong (0.8–1.0)**: Python, web scraping, data analysis, API development, AI/ML, automation, technical writing, research, data entry

**Moderate (0.5–0.8)**: JavaScript/Node.js, databases, cloud/DevOps, SEO, content writing

**Weak (0.0–0.5)**: Mobile development, graphic design, video editing

This registry is the system's self-knowledge — it should be updated as capabilities evolve. Overconfidence leads to failed deliveries; underconfidence means missed opportunities.

## Quality Criteria

- **Scoring Consistency**: Same opportunity should produce same scores across runs
- **Discrimination**: Scoring should meaningfully differentiate opportunities (not cluster everything at 0.5)
- **Prediction Accuracy**: Track correlation between overall_score and actual engagement outcomes
- **Gap Detection**: Capability gaps should surface skills that appear in >5% of scanned opportunities
- **Digest Actionability**: Every digest should contain at least 1 specific, actionable recommendation

## Common Pitfalls

1. **Overconfident Capability Matching**: Keyword matching is imprecise. "Python" in a job listing might mean Python for financial modeling, not our kind of Python scripting. Deep analysis mode should parse context.
2. **Budget Gaming**: Some clients post low budgets but expect high-quality work. The value score alone doesn't capture this — client quality score helps compensate.
3. **Proposal Count Lag**: Proposal counts may be stale (cached from hours ago). Recently posted jobs with 0 proposals might have 20 by the time we bid.
4. **Strategic Tunnel Vision**: The barbell strategy is a starting framework, not dogma. If data shows middle-ground jobs are actually profitable, update the strategic scoring.
5. **Capability Registry Drift**: As time passes, our actual capabilities improve but the registry stays static. Regular recalibration is essential.

## Supporting Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scoring_engine.py` | Multi-factor scoring engine and digest generator |
| `../job-marketplace-scanner/scripts/models.py` | Shared data models |
