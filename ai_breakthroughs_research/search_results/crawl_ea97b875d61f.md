---
title: "Untitled"
source: https://lesswrong.com/posts/YABG5JmztGGPwNFq2/ai-futures-timelines-and-takeoff-model-dec-2025-update
date: unknown
description: ""
word_count: 20734
---

AI Futures Timelines and Takeoff Model: Dec 2025 Update
30 min read
•
Why do timelines and takeoff modeling?
•
Why our approach to modeling? Comparing to other approaches
•
[AGI[1] timelines forecasting methods](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#AGI_1__timelines_forecasting_methods>)
•
Trust the experts
•
Intuition informed by arguments
•
Revenue extrapolation
•
Compute extrapolation anchored by the brain
•
Capability benchmark trend extrapolation
•
Post-AGI takeoff forecasts
•
How our model works
•
Stage 1: Automating coding
•
Stage 2: Automating research taste
•
Stage 3: The intelligence explosion
•
Timelines and takeoff forecasts
•
Eli
•
Daniel
•
Comparison to our previous (AI 2027) timelines and takeoff models
•
Timelines to Superhuman Coder (SC)
•
Takeoff from Superhuman Coder onward
AIFrontpage
2025 Top Fifty: 14%
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#>)
# 134
# AI Futures Timelines and Takeoff Model: Dec 2025 Update
by elifland, bhalstead, Alex Kastner, Daniel Kokotajlo
31st Dec 2025
AI Alignment Forum
30 min read
30
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#>)
# 134
# Ω 46
We’ve significantly upgraded our timelines and takeoff model! It predicts when AIs will reach key capability milestones: for example, Automated Coder / AC (full automation of coding) and superintelligence / ASI (much better than the best humans at virtually all cognitive tasks). This post will briefly explain how the model works, present our timelines and takeoff forecasts, and compare it to our previous (AI 2027) models (spoiler: the AI Futures Model predicts longer timelines to full coding automation than our previous model by about 3-5 years, in significant part due to being less bullish on pre-full-automation AI R&D speedups).
If you’re interested in playing with the model yourself, the best way to do so is via this interactive website: **aifuturesmodel.com**.

_If you’d like to skip the motivation for our model to an explanation for how it works, go_ _here_ _, The website has a more in-depth explanation of the model (starts_ _here_ _; use the diagram on the right as a table of contents), as well as_ _our forecasts_ _._
# Why do timelines and takeoff modeling?
The future is very hard to predict. We don't think this model, or any other model, should be trusted completely. The model takes into account what we think are the most important dynamics and factors, but it doesn't take into account everything. Also, only some of the parameter values in the model are grounded in empirical data; the rest are intuitive guesses. If you disagree with our guesses, you can change them above.
Nevertheless, we think that modeling work is important. Our overall view is the result of weighing many considerations, factors, arguments, etc.; a model is a way to do this transparently and explicitly, as opposed to implicitly and all in our head. By reading about our model, you can come to understand why we have the views we do, what arguments and trends seem most important to us, etc.
The future is uncertain, but we shouldn’t just wait for it to arrive. If we try to predict what will happen, if we pay attention to the trends and extrapolate them, if we build models of the underlying dynamics, then we'll have a better sense of what is likely, and we'll be less unprepared for what happens. We’ll also be able to better incorporate future empirical data into our forecasts.
In fact, the improvements we’ve made to this model, as compared to our timelines model at the time we published AI 2027 (Apr 2025), have resulted in a roughly 3-5 year shift in our median for full coding automation. This has primarily come from improving our modeling of AI R&D automation. These modeling improvements have resulted in a larger change in our views than the new empirical evidence that we’ve observed. You can read more about the shift below.
# Why our approach to modeling? Comparing to other approaches
## AGI[[1]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-1>) timelines forecasting methods
### Trust the experts
Unfortunately, there is nothing close to an expert consensus, and it doesn’t seem like most experts have thought much about AGI forecasting (e.g. a 2023 survey observed huge framing effects depending on whether they asked for probabilities of milestones being achieved by certain years, or instead asked for years that correspond to percentiles). That 2023 survey of AI academics got an AGI median of 2047 or 2116, depending on the definition.[[2]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-2>) There’s also this aggregation of Metaculus and Manifold markets which estimates 50% by 2030. As for the people building the technology, they tend to be more bullish; the most extreme among them (Anthropic and OpenAI) say things like 2027 and 2028. For a survey of older predictions and how they’ve fared, see this.
Given that experts disagree with each other and mostly seem to have not thought deeply about AGI forecasting, we think it’s important to work to form our own forecast.
### Intuition informed by arguments
Can the current paradigm scale to AGI? Does it lack something important, like common sense, true original thinking, or online/continual learning (etc.)? Questions like these are very important and there are very many of them, far too many to canvas here. The way this method works is that everyone ingests the pile of arguments and considerations and makes up their own minds about which arguments are good and how they weigh against each other. This process inherently involves intuition/subjective-judgment, which is why we label it as “intuition.”
Which is not to denigrate it! We think that any AI forecaster worth their salt must engage in this kind of argumentation, and that generally speaking the more facts you know, the more arguments you’ve considered and evaluated, the more accurate your intuitions/vibes/judgments will become. Also, relatedly, your judgment about which models to use, and how much to trust them, will get better too. Our own all-things-considered views are only partially based on the modelling we’ve done; they are also informed by intuitions.
But we think that there are large benefits to incorporating quantitative models into our forecasts: it’s hard to aggregate so many considerations into an overall view without using a quantitative framework. We’ve also found that quantitative models help prioritize which arguments are most important to pay attention to. And our best guess is that overall, forecasts by quantitative trend extrapolation have a better historical track record than intuitions alone.
### Revenue extrapolation
Simple idea: extrapolate AI revenue until it’s the majority of world GDP. Of course, there’s something silly about this; every previous fast-growing tech sector has eventually plateaued… That said, AI seems like it could be the exception, because in principle AI can do everything. Now that AI is a major industry, we think this method provides nonzero evidence. According to this Epoch dataset, frontier AI company revenue is something like $20B now and growing around 4.1x/yr. This simple extrapolation gets to $100T annualized revenue around the end of 2031.[[3]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-3>)
We give weight to revenue extrapolation in our all-things-considered views, but on the other hand revenue trends change all the time and we’d like to predict the underlying drivers of how it might change. Also, it’s unclear what revenue threshold counts as AGI. Therefore, we want to specifically extrapolate AI capabilities.
### Compute extrapolation anchored by the brain
The basic idea is to estimate how much compute it would take to get AGI, anchored by the human brain. Then predict that AGI will happen when we have that much compute. This approach has gone through a few iterations:
  1. Hans Moravec, Ray Kurzweil, and Shane Legg pioneered this method, predicting based on the amount of operations per second that the human brain does. Moravec predicted AGI in 2010 in 1988, then revised it to 2040 in 1999. Kurzweil and Legg each predicted AGI in the late 2020s in about 2000.[[4]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-4>)
  2. Ajeya Cotra’s 2020 biological anchors report instead predicted AGI[[5]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-5>)based on how much compute it would take to train the human brain. Cotra also estimated how much algorithmic progress would be made, converting it into the equivalent of training compute increases to get “effective compute”. The report predicted a median of 2050.

Davidson’s Full Takeoff Model and Epoch’s GATE used the same method as bio anchors to determine the AGI training compute requirement, but they also modeled how AI R&D automation would shorten timelines. They modeled automation by splitting up AI software and hardware R&D into many tasks, then forecasting the effective compute gap between 20% task automation and 100% automation. The percentage of tasks automated, along with experiment compute and automation compute, determine the magnitude of inputs to AI R&D. These inputs are converted to progress in software efficiency using a semi-endogeneous growth model. Software efficiency is then multiplied by training compute to get effective compute.
At the time the FTM was created it predicted AGI in 2040, with the parameter settings chosen by Davidson. But both compute and algorithmic progress has been faster than they expected. When the FTM is updated to take into account this new data, it gives shorter medians in the late 2020s or early 2030s. Meanwhile, with GATE’s median parameters, it predicts AGI in 2034.
Overall, this forecasting method seems to us to have a surprisingly good track record: Moravec, Kurzweil, and Legg especially look to have made predictions a long time ago that seem to hold up well relative to what their contemporaries probably would have said. And our model follows these models by modeling training compute scaling, though in most of our simulations the majority of progress toward AGI comes from software.
### Capability benchmark trend extrapolation
This is our approach! We feel that now, in 2025, we have better evidence regarding the AGI effective compute requirement than comparisons to the human brain: specifically, we can extrapolate AIs’ performance on benchmarks. This is how the timelines portion of our model works. We set the effective compute required for AGI by extrapolating METR’s coding time horizon suite, METR-HRS.
We think it’s pretty great. Benchmark trends sometimes break, and benchmarks are only a proxy for real-world abilities, but… METR-HRS is the best benchmark currently available for extrapolating to very capable AIs, in our opinion. We think it’s reasonable to extrapolate that straight line into the future for at least the next few years.[[6]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-6>)
METR itself did a simple version of this extrapolation which assumed exponential growth in time horizons in calendar time. But this doesn’t account for AI R&D automation, changes to human labor or compute growth, or the possibility of time horizon doublings getting easier or harder at higher horizons.[[7]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-7>)
Our previous timelines model took all of these into account, though more crudely than our new AI Futures Model. Our previous model with median parameters predicted superhuman coder (SC) medians of 2027 to 2028, while our new model predicts 2032. The difference mostly comes from improvements to how we’re modeling AI R&D automation. See below for details.
## Post-AGI takeoff forecasts
The literature on forecasting how capabilities progress after full automation of AI R&D is even more nascent than that which predicts AGI timelines. Past work has mostly fallen into one of two buckets:
  1. Qualitative arguments or oversimplified calculations sketching why takeoff might be fast or slow: for example, Intelligence Explosion Microeconomics by Eliezer Yudkowsky (arguing for fast takeoff) or Takeoff speeds by Paul Christiano (arguing for slow takeoff).[[8]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-8>)
  2. Models of the software intelligence explosion (SIE), i.e. AIs getting faster at improving its own capabilities without additional compute: in particular, How quick and big would a software intelligence explosion be? by Davidson and Houlden.[[9]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-9>)

As in timelines forecasting, we think that qualitative arguments are valuable but we think that modeling is a useful complement to qualitative arguments.
Davidson and Houlden focuses primarily on trends of how much more efficiently AIs have been able to achieve the same performance when determining whether there will be an SIE.[[10]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-10>)Meanwhile, we focus on estimates of the quality of AIs’ research taste, i.e. how good the AI is at choosing research directions, selecting and interpreting experiments, etc. We think that focusing on research taste quality is a more useful lens from which to view a potential SIE. If there’s an SIE we expect that it will primarily be driven by improvements in research taste.
Furthermore, because our takeoff model is integrated into a more expansive quantitative model, we have other advantages relative to Davidson and Houlden. For example, we can account for increases in the AGI project’s compute supply.[[11]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-11>)
# How our model works
On the web app, there’s an interactive diagram explaining the parts of the model and how they relate to each other, with a corresponding full model explanation:Here we’ll just give a brief overview.
Our model’s primary output is the trajectory of AIs’ abilities to automate and accelerate AI software R&D. We also include milestones tracking general capabilities, but these are calculated very roughly.
Our model can intuitively be divided into 3 stages. **Although the same formulas are used in Stages 1, 2, and 3** , new dynamics emerge at certain milestones (Automated Coder, Superhuman AI Researcher), and so these milestones delineate natural stages.

## Stage 1: Automating coding
First we’ll discuss how our model predicts when coding will be fully automated. Stage 1 predicts when an Automated Coder (AC) arrives.
**Automated Coder (AC)**. An AC can fully automate an AGI project's coding work, replacing the project's entire coding staff.[[12]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-12>)
Our starting point is to take the METR graph and extrapolate it exponentially, as METR does, then make a guess about what agentic coding time horizon would correspond to the AC milestone. This gives us an estimated date for when AC will be achieved.
However, this simple extrapolation misses out on many important factors, such as:
  * **The inputs to AI progress — most notably compute, but also labor, data, etc. — won’t keep growing at the same rates forever.** There’s a significant chance that growth rates will slow in the near future e.g. as we run up against limits of chip production, investment, recruiting pipelines, energy, etc. This could cause the trend to bend downwards.
  * **Automation of AI R &D.** Already many AI researchers claim that AI is accelerating their work.[[13]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-13>) The extent to which it is _actually_ accelerating their work is unfortunately unclear, but probably there is a nonzero effect already and probably this acceleration effect will increase as AIs become more capable. This could cause the trend to bend upwards.
  * **Superexponential time horizon growth (independent from AI R &D automation).** Eventually there will be AI systems which outperform humans at all horizon lengths; therefore, the trend should _eventually_ shoot to infinity.[[14]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-14>) Therefore, we think we should use a superexponential trend rather than an exponential trend. (This is confusing and depends on how you interpret horizon lengths, see here for more discussion. If you disagree with this, our model allows you to use an exponential trend if you like, or even subexponential.)

**Our model up through AC still centrally involves the METR trend,**[[15]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-15>) but it attempts to incorporate the above factors and more. It also enables us to better represent/incorporate uncertainty, since we can do Monte Carlo simulations with different parameter settings.
## Stage 2: Automating research taste
Besides coding, we track one other type of skill that is needed to automate AI software R&D: research taste. While automating coding makes an AI project faster at implementing experiments, automating research taste makes the project better at setting research directions, selecting experiments, and learning from experiments.
Stage 2 predicts how quickly we will go from an automated coder (AC) to a Superhuman AI researcher (SAR), an AI with research taste matching the top human researcher.
**Superhuman AI Researcher (SAR):** A SAR can fully automate AI R&D, making all human researchers obsolete.[[16]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-16>)
The main drivers of how quickly Stage 2 goes is:
  1. **How much automating coding speeds up AI R &D.** This depends on a few factors, for example how severely the project gets bottlenecked on experiment compute.
  2. **How good AIs' research taste is at the time AC is created.** If AIs are better at research taste relative to coding, Stage 2 goes more quickly.
  3. **How quickly AIs get better at research taste.** For a given amount of inputs to AI progress, how much more value does one get per experiment?

## Stage 3: The intelligence explosion
Finally, we model how quickly AIs are able to self-improve once AI R&D is fully automated and humans are obsolete. The endpoint of Stage 3 is asymptoting at the limits of intelligence.
The primary milestones we track in Stage 3 are:
  1. **Superintelligent AI Researcher (SIAR).** The gap between a SIAR and the top AGI project human researcher is 2x greater than the gap between the top AGI project human researcher and the median researcher.[[17]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-17>)
  2. **Top-human-Expert-Dominating AI (TED-AI).** A TED-AI is at least as good as top human experts at virtually all cognitive tasks. (Note that the translation in our model from AI R&D capabilities to general capabilities is very rough.)[[18]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-18>)
  3. **Artificial Superintelligence (ASI).** The gap between an ASI and the best humans is 2x greater than the gap between the best humans and the median professional, at virtually all cognitive tasks.[[19]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-19>)

In our simulations, we see a wide variety of outcomes ranging from a months-long takeoff from SAR to ASI, to a fizzling out of the intelligence explosion requiring further increases in compute to get to ASI.
To achieve a fast takeoff, there usually needs to be a feedback loop such that each successive doubling of AI capabilities takes less time than the last. In the fastest takeoffs, this is usually possible via a _taste-only singularity_ , i.e. the doublings would get faster solely from improvements in research taste (with no improvements in coding, or extra compute). Whether a taste-only singularity occurs depends on which of the following dominates:
  1. **The rate at which (experiment)****ideas become harder to find****.** Specifically, how much new “research effort” is needed to achieve a given increase in AI capabilities.
  2. **How quickly AIs' research taste improves.** For a given amount of inputs to AI progress, how much more value does one get per experiment?

Continued improvements in coding automation matter less and less, as the project gets bottlenecked by their limited supply of experiment compute.
# Timelines and takeoff forecasts
The best place to view our results is at <https://www.aifuturesmodel.com/forecast>.
In this section we will discuss both our model’s outputs and our all-things-considered views. As previously mentioned, we are uncertain, and don’t blindly trust our models. Instead we look at the results of the model but then ultimately make adjustments based on intuition and other factors. Below we describe the adjustments that we make on top of this model, and the results.
### Eli
Here is the model’s output with my parameters along with my all-things-considered views.
To adjust for factors outside of the model, I’ve l**engthened timelines (median from late 2030 to mid 2032),** driven primarily by unknown model limitations and mistakes and the potential for data bottlenecks that we aren’t modeling. In summary:
  1. **Unknown model limitations and mistakes.** With our previous (AI 2027) timelines model, my instinct was to push my overall forecasts longer due to unknown unknowns, and I’m glad I did. My median for SC was 2030 as opposed to the model’s output of Dec 2028, and I now think that the former looks more right. I again want to lengthen my overall forecasts for this reason, but less so because our new model is much more well-tested and well-considered than our previous one, and is thus less likely to have simple bugs or unknown simple conceptual issues.
  2. **Data bottlenecks.** Our model implicitly assumes now that any data progress is proportional to algorithmic progress. But data in practice could be either more or less bottlenecking. My guess is that modeling data would lengthen timelines a bit, at least in cases where synthetic data is tough to fully rely upon.

I will also increase the 90th percentile from 2062. My all-things-considered distribution is: 10th percentile 2027.5, 50th percentile 2032.5, 90th percentile 2085. You can see all of the adjustments that I considered in this supplement.
Now I’ll move on to takeoff.
To get my all-things-considered views I: **increase the chance of fast takeoff a little (I change AC to ASI in <1 year from 26% to 30%), and further increase the chance of <3 year takeoffs (I change the chance of AC to ASI in <3 years from 43% to 60%).**
The biggest reasons I make my AI-R&D-specific takeoff a bit faster are:
  1. **Automation of hardware R &D, hardware production, and general economic automation.** We aren’t modeling these, and while they have longer lead times than software R&D, a year might be enough for them to make a substantial difference.
  2. **Shifting to research directions which are less compute bottlenecked might speed up takeoff, and isn’t modeled.** Once AI projects have vast amounts of labor, they can focus on research which loads more heavily on labor relative to experiment compute than current research.

(1) leads me to make a sizable adjustment to the tail of my distribution. I think modeling hardware and economic automation would make it more likely that if there isn’t taste-only singularity, we still get to ASI within 3 years.
I think that, as with timelines, for takeoff unknown limitations and mistakes in expectation point towards things going slower. But unlike with timelines, there are counter-considerations that I think are stronger. You can see all of the adjustments that I considered in this supplement.
### Daniel
First, let me say a quick prayer to the spirit of rationality, who infrequently visits us all:
On the subject of timelines, I don’t immediately know whether my all-things-considered view should be more or less bullish than the model. Here are a few considerations that seem worth mentioning to me:
  * First of all, this model is in-the-weeds / gearsy. (Some people might call it “inside-viewy” but I dislike that term.) I think it’s only appropriate to use models like this if you’ve already thought through more straightforward/simple considerations like “Is the phenomena in question [AGI] even possible at all? Do serious experts take it seriously? Are there any obvious & solid arguments for why this is a nothingburger?” I have thought through those kinds of things, and concluded that yes, AGI arriving in the next decade seems a very serious possibility indeed, worthy of more gearsy investigation. If you disagree or are curious what sorts of considerations I’m talking about, a partial list can be found in this supplement.
  * I think this model is the best model of AI R&D automation / intelligence explosion that currently exists, but this is a very poorly understood phenomenon and there’s been very little attention given to it, so I trust this model less when it comes to takeoff speeds than I do when it comes to timelines. (And I don’t trust it that much when it comes to timelines either! It’s just that there isn’t any single other method I trust more…)
  * I notice a clash between what the model says and my more intuitive sense of where things are headed. I think probably it is my intuitions that are wrong though, which is why I’ve updated towards longer timelines; I’m mostly just going with what the model says rather than my intuitions. However, I still put some weight on my intuitive sense that, gosh darn it, we just aren’t more than 5 years away from the AC milestone – think about how much progress has happened over the last 5 years! Think about how much progress in agentic coding specifically has happened over the last year!
  * More detail on vibes/intuitions/arguments:
    * I’ve been very unimpressed by the discourse around limitations of the current paradigm. The last ten years have basically been one vaunted limitation after another being overcome; Deep Learning has hit a wall only in the sense that Godzilla has hit (and smashed through) many walls.
    * However, two limitations do seem especially plausible to me: Online/continual learning and data efficiency. I think there has been some progress in both directions over the past years, but I’m unclear on how much, and I wouldn’t be _that_ surprised if it’s only a small fraction of the distance to human level.
    * That said, I also think it’s plausible that human level online/continual learning is only a few years away, and likewise for data-efficiency. I just don’t know. (One data point: claim from Anthropic researcher)
    * Meanwhile, I’m not sure either of those things are _necessary_ for AI R&D to accelerate dramatically due to automation. People at Anthropic and OpenAI already report that things are starting to speed up due to AI labor, and I think it’s quite plausible that massively scaled-up versions of current AI systems (trained on OOMs more diverse RL environments, including many with OOMs longer horizon lengths) could automate all or almost all of the AI R&D process. The ability to learn from the whole fleet of deployed agents might compensate for the data-inefficiency, and the ability to manage huge context window file systems, update model weights regularly, and quickly build and train on new RL environments might compensate for lack of continual learning.
    * And once AI accelerates dramatically due to automation, paradigm shifts of the sort mentioned above will start to happen soon after.
    * Summing up: Qualitatively, my intuitive sense of what’s going to happen in the next few years is, well, basically the same sequence of events described in AI 2027, just maybe taking a year or two longer to play out, and with various other minor differences (e.g. I don’t expect any one company to have as much of a lead as OpenBrain does in the scenario).
  * I’m also quite nervous about relying so much on the METR horizon trend. I think it’s the best _single_ source of evidence we have, but unfortunately it’s still pretty limited as a source of evidence.
    * It is uncertain how it’ll extrapolate into the future (exponential or superexponential? If superexponential, _how_ superexponential? Or should we model new paradigms as a % chance per year of changing the slope? What even is the slope right now, it seems to maybe be accelerating recently?)
    * …and also uncertain how to interpret the results (is a 1 month 80% horizon enough? Or do we need 100 years?).
    * There are also some imperfections in the methodology which complicate things. E.g. if I understand correctly the human baseliners for the various tasks were not of the same average skill level, but instead the longer-horizon tasks tended to have higher-skill human baseliners. Also, the sigmoid fit process is awkwardly non-monotonic, meaning there are some cases in which a model getting strictly better (/worse) at some bucket of tasks can decrease (/increase) its METR-reported horizon length! My guess is that these issues don’t make a huge difference in practice, but still. I hope that a year from now, it becomes standard practice for many benchmark providers to provide information about how long it took human baseliners to complete the tasks, and the ‘skill level’ of the baseliners. Then we’d have a lot more data to work with.
    * Also, unfortunately, METR won’t be able to keep measuring their trend forever. It gets exponentially more expensive for them to build tasks and collect human baselines as the tasks get exponentially longer. I’m worried that by 2027, METR will have basically given up on measuring horizon lengths, which is scary because then we might not be able to tell whether horizon lengths are shooting up towards infinity or continuing to grow at a steady exponential pace.
    * I think a much better trend to extrapolate, if only we had the data, would be coding uplift. If we had e.g. every 6 months for the past few years a high-quality coding uplift study, we could then extrapolate that trend into the future to predict when e.g. every engineer would be a 10x engineer due to AI assistance. (Then we’d still need to predict when research taste would start to be noticeably uplifted by AI / when AIs would surpass humans in research taste; however, I think it’s a reasonable guess right now that when coding is being sped up 10x, 100x, etc. due to highly autonomous AI coding agents, research taste should be starting to improve significantly as well.[[20]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-20>) At least I feel somewhat better about this guess than I do about picking any particular threshold of METR horizon length and guessing that it corresponds to a particular level of experiment selection skill, which is what we currently do.)
  * Relatedly, I’m also interested in the simple method of extrapolating AI revenue growth trends until AI revenue is most of the world economy. That seems like a decent proxy for when AGI will be achieved. I trust this method less than our model for obvious reasons, but I still put some weight on it. What does it say? Well, it says “Early 2030s.” OK.
  * I’m also interested in what our model says with a pure exponential trend extrapolation for METR instead of the superexponential (I prefer the superexponential on theoretical grounds, though note also that there seems to be a recent speeding up of the METR trend and a corresponding speedup in the trend on other benchmarks). Pure exponential trend, keeping my other parameters fixed, gets to AC 5 years later, in 2034. That said, if we use the more recent ~4 month doubling time that seems to characterize the RL era, even an exponential trend gets to AC in 2030, keeping other parameters fixed. I’m not sure I should keep my other parameters fixed though, particularly the AC coding time horizon requirement seems kinda up in the air since the change to exponential slope corresponds to a change in how I interpret horizon lengths in general.[[21]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-21>)
    * One factor weighing on my mind is the apparent recent speedup in AI capabilities progress–e.g. the slope of the METR trend seems notably higher since 2024 than it was before. This could be taken as evidence in favor of a (more) superexponential trend overall…
    * However, I’m currently leaning against that interpretation, for two reasons. First, the speedup in the trend isn’t just for the METR trend, it’s also for other benchmarks, which are not supposed to be superexponential. Secondly, there’s another very plausible explanation for what’s going on, which is that starting in 2024 the companies started scaling up RL a lot. But they won’t be able to keep scaling it at the same pace, because they’ll run into headwinds as RL becomes the majority of training compute. So on this view we should expect the rate of growth to revert towards the long-run average starting about now (or however long it takes for RL compute to become the majority of total training compute).
    * That said, I still think it’s plausible (though not likely) that actually what we are seeing is the ominous uptick in the rate of horizon length growth that is predicted by theory to happen a year or two before horizon lengths shoot to infinity.
  * Also, like Eli said above, I feel that I should err on the side of caution and that for me that means pushing towards somewhat longer timelines.
  * Finally, I have some private info which pushes me towards somewhat shorter timelines in expectation. My plan is to circle back in a month or three when more info is available and update my views then, and I currently expect this update to be towards somewhat shorter timelines though it’s unclear how much.

Weighing all these considerations, I think that my all-things-considered view on timelines will be to (1) push everything back one year from what the model says. So, my median for automated coder milestone 2030 instead of 2029, my median for superhuman AI researcher milestone 2031 instead of 2030.
In addition to that, I’ll (2) increase the uncertainty in both directions somewhat, so that there’s a somewhat greater chance of things going crazy in the next year (say, 9% by EOY 2026) and also a somewhat greater chance of things taking decades longer (say, still 6% that there’s no AGI even in 2050).
So, here’s my all-things-considered distribution as of today, Dec 30 2025:

On takeoff speeds:
I think my thoughts on this are pretty similar to Eli’s, modulo differences implied by our different parameter settings. Basically, take what the model (with my parameters) says, and then shift some probability mass away from the slower end and put it on the faster end of the range.
Also, whereas our model says that takeoff speeds are correlated with timelines such that shorter timelines also tends to mean faster takeoff, I’m not sure that’s correct and want to think about it more. There’s a part of me that thinks that on longer timelines, takeoff should be extremely fast due to the vast amounts of compute that will have piled up by then and due to the compute-inefficiency of whatever methods first cross the relevant thresholds by then.
So here’s a quick distribution I just eyeballed:
What info I’ll be looking for in the future & how I’ll probably update:
  * Obviously, if benchmark trends (especially horizon length) keep going at the current pace or accelerate, that’ll be an update towards shorter timelines. Right now I still think it’s more likely than not that there’ll be a slowdown in the next year or two.
  * I’m eager to get more information about coding uplift. When we have a reliable trend of coding uplift to extrapolate, I’ll at the very least want to redo my estimates of the model parameters to fit that coding uplift trend, and possibly I’d want to rethink the model more generally to center on coding uplift instead of on horizon length.
  * If AI revenue growth stays strong (e.g. 4xing or more in 2026) that’s evidence for shorter timelines vs. if it only grows 2x or less that’s evidence for longer timelines.
  * I’m eager to get more information about the ‘slope’ of the performance-as-a-function-of-time graph for various AI models, to see if it’s been improving over time and how far away it is from human performance. (See this discussion) This could potentially be a big update for me in either direction.
  * As for takeoff speeds, I’m mostly interested in thinking more carefully about that part of our model and seeing what improvements can be made.[[22]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-22>) I don’t think there’ll be much empirical evidence one way or another in the next year. Or rather, I think that disputes about the proper way to model takeoff matter more than evidence about the value of various parameters, at this stage. That said, I’ll be keen to get better estimates of some of the key parameters too.
  * Of course I’m also interested to hear the feedback/criticism/etc. from others about the model and the parameters and the overall all things considered view. I wouldn’t be surprised if I end up changing my mind significantly on the basis of arguments I haven’t thought of yet.
  * …this list is nowhere near exhaustive but that’s enough for now I guess.

# Comparison to our previous (_AI 2027_) timelines and takeoff models
These sections focus specifically on the model results with Eli’s parameter estimates (for both the AI Futures Model and the AI 2027 model).
## Timelines to Superhuman Coder (SC)
This section focuses on timelines to _superhuman coder (SC)_ , which was our headline milestone in our AI 2027 timelines model: an SC represents an AI that autonomously is as productive as an AGI project modified to have all coders as competent as their best, speeding them each up by 30x, and getting 30 copies of each of them.[[23]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-23>)
We’ll discuss only the AI 2027 time horizon extension model in this section, due to it being simpler than the benchmarks and gaps version.[[24]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-24>) Below we compare the forecasted distribution of the AI 2027 model against that of the AI Futures Model._Edited Jan 8: updated the above figure and below description to fix an issue, moving the new model’s SC timelines back slightly._
We see that the AI Futures Model median is 5 years later than the AI 2027 model, and that it assigns a 9% chance that SC happens before the time horizon extension’s median. From now onward, we will focus on the trajectory with median parameters rather than distributions of SC dates, for ease of reasoning.
The AI 2027 time horizon extension model, with parameters set to their median values, predicts SC in Jan 2027 given superexponential-in-effective-compute time horizon growth, and SC in Sep 2028 given exponential time horizon growth. Meanwhile, the new model with median parameters predicts SC in Dec 2031. This is a 3.25-5 year difference! From now on we’ll focus on the 5 year difference, i.e. consider superexponential growth in the time horizon extension model. This is a closer comparison because in our new model, our median parameter estimate predicts superexponential-in-effective-compute time horizon growth.
The biggest reason for this difference is that we model pre-SC AI R&D automation differently, which results in such automation having a much smaller effect in our new model than in the AI 2027 one. The 5 year increase in median comes from:
  1. **Various parameter estimate updates: ~1 year slower.** These are mostly changes to our estimates of parameters governing the time horizon progression. Note that 0.6 years of this is from the 80% time horizon progression being slower than our previous median parameters predicted, but since we are only looking at 80% time horizons we aren’t taking into account the evidence that Opus 4.5 did well on 50% time horizon.
  2. **Less effect from AI R &D automation pre-SC: ~2 years slower.** This is due to:
    1. **Taking into account diminishing returns:** The AI 2027 timelines model wasn’t appropriately taking into account diminishing returns to software research. It implicitly assumes that exponential growth in software efficiency is not getting “harder” to achieve, such that if AIs gave a software R&D uplift of 2x in perpetuity, the software efficiency growth rate would speed up by 2x in perpetuity. We didn’t realize this implicit assumption and have now fixed it.
    2. **Less AI software R &D uplift from pre-SC AIs:** The interpolation method used to get AI software R&D uplift values in the AI 2027 model in between present day and SC gave much higher intermediate values than the uplift we end up with in our new model. We previously modeled 50% of the way to SC in effective compute OOMs as resulting in 50% of the way to SC in terms of log(uplift), but our new model is more pessimistic. Partially, this is because the AI 2027 model had a bug in how AI software R&D was interpolated between present AIs and SC.. But that only accounts for half of the difference, the other half comes from us choosing an interpolation method that was more optimistic about pre-SC speedups than the AI Futures Model.
  3. **Compute and labor input time series adjustments: ~1 year slower.** That is, we now project slower growth in the leading AI project’s compute amounts and in their human labor force. Read about the AI Futures Model’s input time series here.
  4. **Modeling experiment compute: ~1 year slower.** Previously we were only modeling labor as an input to software progress, not experiment compute.

You can read more about these changes and their effects in our supplementary materials.
## Takeoff from Superhuman Coder onward
The AI Futures Model predicts a slower median takeoff than our AI 2027 takeoff model. Below we graph each of their forecasted distributions for how long it will take to go from SC to ASI._Edited Jan 8: updated the above figure and below description to fix an issue, moving the new model’s takeoff to be a bit slower._
We see that while the AI Futures Model’s median is longer than the AI 2027 one, it still puts 38% probability of takeoff as fast as AI 2027’s median. On the other hand, the AI Futures Model’s cumulative probability gets closer to the AI 2027 model as the AC to ASI year amount increases. The new model is less “binary” in the sense that it gives lower probability to very fast or very slow takeoffs. This is because the AI Futures Model models compute increases.[[25]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-25>)
The reason the AI Futures Model gives a lower chance of fast takeoffs is primarily that we rely on a new framework for estimating whether there’s an SIE and how aggressive it is.
Our AI 2027 takeoff model predicted the progression of capabilities post-SC. Its methodology was also fairly simple. First, we enumerated a progression of AI capability milestones, with a focus on AI R&D capabilities, though we think general capabilities will also be improving. Then, for each gap between milestones A and B, we:
  1. **Human-only time:** Estimated the time required to go from milestone A to B if only the current human labor pool were doing software research.
  2. **AI R &D progress multiplier (what we now call AI software R&D uplift, or just AI R&D uplift):** Forecasted how much AI R&D automation due to each of milestones A and B will speed up progress, then run a simulation in which the speedup is interpolated between these speedups over time to get a forecasted distribution for the calendar time between A and B.

In order to estimate some of the human-only time parameters, the AI 2027 takeoff forecast relied on a parameter it called _r_ , which controlled the diminishing returns to AI R&D. It was crudely estimated by backing out the implied _r_ from the first human-only time requirement, which was to get from SC to SAR.
The AI 2027 model assumed that there were no compute increases; under this assumption, if it _r_ >1 then successive doublings of AI R&D uplift (what we previously called progress multiplier) gets faster over time after full AI R&D automation. Others have referred to this possibility as a software intelligence explosion (SIE). In the model, each doubling took about 0.7x as long as the previous: we’ll call the ratio of successive uplift doublings _b_ from here onward, i.e. _b_ <1 means successive doublings are faster and we get an SIE.[[26]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-26>)
In the AI Futures Model, the condition for an SIE is more complicated because we model multiple types of AI R&D; we also include compute increases, departing significantly from the behavior of an SIE. That said, there is a similar understandable concept in our model: a taste-only singularity (TOS). This is the situation in which after full AI R&D automation and with only research taste improvements (no extra coding or compute), successive doublings of AI R&D uplift get faster over time. To make the analysis much simpler, we also ignore the limits of intelligence in our analysis; these usually don’t greatly affect the takeoff to ASI, but they do slow progress down somewhat.
Under these assumptions, we can define a similar _b_ to that analyzed in an SIE.
We estimate _b_ by combining the following parameters:[[27]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-27>)(a) the ratio of top to median researchers' value per selected experiment(b) how quickly AIs improve at research taste as effective compute increases(c) the rate at which software R&D translates into improved software efficiency (intuitively, the rate at which ideas are getting harder to find).
When using this framework, we get a less aggressive result (with our median parameters). Given that (a) was explicitly estimated in the AI 2027 model, and that we have a fairly aggressive estimate of (c) in the new model, implicitly most of the difference in results is coming from (b), how quickly AIs improve at research taste. We estimated this in our new model by looking at historical data on how quickly AIs have moved through the human range for a variety of metrics (more on that here).
With the AI 2027 model’s median parameters, each successive doubling of uplift took roughly 66% of the length of the previous (i.e. b=0.7).[[28]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-28>) The AI Futures Model’s distribution of b is below.

In the AI Futures Model model in the median case, there isn’t a TOS: each doubling would take 20% longer than the previous if taste were the only factor.[[29]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-29>) But we have high uncertainty: 38% of our simulations say that successive doublings get faster, and 17% are at least as aggressive as the AI 2027 model (i.e. _b_ <0.7).[[30]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-30>)
Remember that unlike the AI 2027 model, the AI Futures Model models compute increases; also in practice coding automation contributes some to takeoffs.[[31]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fn-36ucBJAcGi2mnJXRS-31>) Therefore, at similar levels of the separate _b_ s we’ve defined here, takeoff in the AI Futures Model is faster.
Faster takeoffs are also correlated in our model with shorter timelines: when we filter for simulations that achieve SC in 2027, 35% of them have a _b_ lower than the AI 2027 model’s median parameters. This is because some parameters lead to larger effects from automation both before and after SC, and furthermore we specified that there be correlations between parameters that govern how quickly coding abilities improve, and how quickly research taste abilities improve.
For further analysis of the differences between our AI 2027 and new takeoff models, see our supplementary materials.
  1. AGI stands for Artificial General Intelligence, which roughly speaking means AI that can do almost everything. Different people give different definitions for it; in our work we basically abandon the term and define more precise concepts instead, such as AC, SIAR, TED-AI, etc. However, we still use the term AGI when we want to vaguely gesture at this whole bundle of concepts rather than pick out one in particular. For example, we’ve titled this section “AGI timelines…” and the next section “Post-AGI takeoff…” because this section is about estimating how many years there’ll be until the bundle of milestones starts to be reached, and the next section is about estimating what happens after some of them have already been reached. ↩︎
  2. 2047 for “unaided machines outperforming humans in every possible task”, and 2116 for “all human ↩︎
  3. Some have also done extrapolations of Gross World Product, such as David Roodman’s Modeling the Human Trajectory. ↩︎
  4. More details: ↩︎
  5. Technically, the report predicted the arrival of Transformative AI, or TAI, which was defined as having at least as big of an impact as the Industrial Revolution. ↩︎
  6. Rule of thumb inspired by Lindy’s Law: It’s reasonable to guess that a trend will continue for about as long as it’s been going so far. We wouldn’t dream of confidently extrapolating this trend for thirty years, for example. (We do in fact run the model into the 2050s and onward in our Monte Carlos, but we acknowledge that the probability of reality diverging dramatically from the model increases with the duration of the extrapolation.) ↩︎
  7. Peter Wildeford has a model which has the possibility of doublings getting easier or harder, but does not model AI R&D automation or changes to labor or compute growth. ↩︎
  8. See also: Most AI value will come from broad automation, not from R&D | Epoch AI ↩︎
  9. GATE and the Full Takeoff Model also model the progression after full AI R&D automation, but neither of their authors claim that their model is intended to do it well. ↩︎
  10. These estimates are then shaded up to account for capability improvements at the same compute level in addition to efficiency improvements at the same performance level. This adjustment brings the methodology closer to ours, but still we think it’s helpful to focus specifically on research taste skills. And finally, in Davidson and Houlden, everything is converted to the units of gains in the number of parallel workers, which we view as a much less natural unit than research taste quality. ↩︎
  11. Among other advantages of having an integrated model: our model itself already bakes in most of the various adjustments that Davidson and Houlden did ad-hoc to their estimate of r, and we can generally ensure reasonable starting conditions (as opposed to Davidson and Houlden’s gradual boost). ↩︎
  12.  _Our model operationalizes AC as follows:_ An AC, if dropped into present day, would be as productive on their own as only human coders with no AIs. That is, you could remove all human coders from the AGI project and it would go as fast as if there were only human coders. The project can use 5% of their compute supply to run ACs. ↩︎
  13. See especially this Anthropic survey of researchers claiming >100% productivity improvements, but also this METR uplift study which found that people systematically overestimate the amount of uplift they were getting from AI assistance. ↩︎
  14. That is, if we think that eventually there will be an AI system which outperforms humans at all horizon lengths, then that means the trend must shoot to infinity in finite time. ↩︎
  15. That is, the part of our model that deals with AI timelines, i.e. the length of the period leading up to the “automated coder” milestone, centrally involves the METR trend. After that milestone is reached, horizon length continues to increase but isn’t directly relevant to the results. The results are instead driven by increases in automated research taste and coding automation efficiency. ↩︎
  16.  _Our model operationalizes SAR as follows_ : if dropped into an AGI project in present day, a SAR would be as good at research taste as if there were only human researchers, who were each made as skilled as the top researcher. ↩︎
  17.  _What do we mean when we say that the gap between a top human researcher and SIAR is 2x greater than that between the median and top human researcher?_ We mean the following. First, let’s define a transformation between AIs’ capability level b and a number of SDs relative to the median as: ↩︎
  18.  _Our model operationalizes TED-AI as follows:_ A TED-AI is an AI system that could, if dropped into the present day & given the resources of a large tech company & three months to prep, fully automate 95% of remote work jobs in the US. It need not be able to do all 95% at the same time (perhaps there isn't enough compute to run enough copies of the TED-AI for that), but it needs to be able to do any 10% of them using only 50% of the US's AI-relevant compute. ↩︎
  19.  _Our model operationalizes ASI as follows:_ An ASI would, if dropped into present day & given the resources of a large tech company & three months to prep, be able to fully automate 95% of remote work jobs in the US to the level where it is qualitatively 2x as much above the best human as the best human is above the median professional. Also, here we define “the median professional” not as the actual median professional but rather as what the the median professional would be, if everyone who took the SATs was professionally trained to do the task. (We standardize the population that is trained to do the task because otherwise the ASI requirement might be quite different depending on the population size and competence levels of the profession. See above regarding how we define the 2x gap.) ↩︎
  20. Spot-checking in our model: Serial coding labor multiplier is basically the square root of parallel coding labor multiplier, and so when I look at my default parameter settings at the point where serial coding labor multiplier is ~10x (May 2030) the AIs have research taste equivalent to the median AI company researcher. Sounds about right to me. ↩︎
  21. I’ve talked about this elsewhere but I generally think that if you don’t like using a superexponential and insist on an exponential, you need to come up with a different interpretation of what it means for a model to have horizon length X, other than the natural one (“A model has horizon length X iff you are better off hiring a human for coding tasks that take humans much longer than X, but better off using the model for coding tasks that take humans much less than X.”) Because on that interpretation, an exponential trend would _never_ get to a model which outperforms humans at coding tasks of any length. But we do think that eventually there will be a model which outperforms humans at tasks of any length. In other words, on the natural interpretation the trend seems likely to go to infinity in finite time eventually. You can try to model that either as a smooth superexponential, or as a discontinuous phase shift… even in the latter case though, you probably should have uncertainty over when the discontinuity happens, such that the probability of it happening by time t increases fairly smoothly with t. ↩︎
  22. For example, I want to think more about serial speed bottlenecks. The model currently assumes experiment compute will be the bottleneck. I also want to think more about the software-only-singularity conditions and whether we are missing something there, and square this with soft upper bounds such as “just do human uploads.” ↩︎
  23. Note that with the new model, we’ve moved toward using _Automated Coder (AC)_ as the headline coding automation milestone, which has a weaker efficiency requirement. ↩︎
  24. That said, we note that the benchmarks and gaps version had longer median SC timelines (Dec 2028). And Eli’s all-things-considered SC median was further still in 2030, though Daniel’s was 2028. ↩︎
  25. That said, we still think that the AI Futures Model gives too low a probability of <10 year takeoffs, because we are not modeling growth in compute due to hardware R&D automation, hardware production automation, or broad economic automation. ↩︎
  26. As discussed here, the AI 2027 model set _r_ =2.77 and 1.56 at different points. _b_ =2^(1/r-1), so _b_ =0.64 to 0.78. ↩︎
  27. See here for a more thorough explanation of how _b_ is calculated from our new model’s parameters. ↩︎
  28. 2^((1/2)-1) gives roughly 0.7. See how we got these numbers here. ↩︎
  29. 2^((0.315/0.248)-1). See the justification for this formula on our website. ↩︎
  30. Note that the minimum b in our model is 0.5. This is a limitation, but in practice, we can still get very fast takeoffs. For example, if b were 0.5 and didn’t change over time, this would lead to a finite-time singularity in 2 times longer than the initial uplift doubling time. ↩︎
  31. This could also be influenced by the uplifts being different for different milestones, or other factors. Unfortunately we haven’t had a chance to do a deep investigation, but a shallow investigation pointed toward compute increases being the primary factor. ↩︎

## New to LessWrong?
Getting Started
FAQ
Library

1.
AGI stands for Artificial General Intelligence, which roughly speaking means AI that can do almost everything. Different people give different definitions for it; in our work we basically abandon the term and define more precise concepts instead, such as AC, SIAR, TED-AI, etc. However, we still use the term AGI when we want to vaguely gesture at this whole bundle of concepts rather than pick out one in particular. For example, we’ve titled this section “AGI timelines…” and the next section “Post-AGI takeoff…” because this section is about estimating how many years there’ll be until the bundle of milestones starts to be reached, and the next section is about estimating what happens after some of them have already been reached. ↩︎
2.
2047 for “unaided machines outperforming humans in every possible task”, and 2116 for “all human ↩︎
3.
Some have also done extrapolations of Gross World Product, such as David Roodman’s Modeling the Human Trajectory. ↩︎
4.
More details: ↩︎
5.
Technically, the report predicted the arrival of Transformative AI, or TAI, which was defined as having at least as big of an impact as the Industrial Revolution. ↩︎
6.
Rule of thumb inspired by Lindy’s Law: It’s reasonable to guess that a trend will continue for about as long as it’s been going so far. We wouldn’t dream of confidently extrapolating this trend for thirty years, for example. (We do in fact run the model into the 2050s and onward in our Monte Carlos, but we acknowledge that the probability of reality diverging dramatically from the model increases with the duration of the extrapolation.) ↩︎
7.
Peter Wildeford has a model which has the possibility of doublings getting easier or harder, but does not model AI R&D automation or changes to labor or compute growth. ↩︎
8.
See also: Most AI value will come from broad automation, not from R&D | Epoch AI ↩︎
9.
GATE and the Full Takeoff Model also model the progression after full AI R&D automation, but neither of their authors claim that their model is intended to do it well. ↩︎
10.
These estimates are then shaded up to account for capability improvements at the same compute level in addition to efficiency improvements at the same performance level. This adjustment brings the methodology closer to ours, but still we think it’s helpful to focus specifically on research taste skills. And finally, in Davidson and Houlden, everything is converted to the units of gains in the number of parallel workers, which we view as a much less natural unit than research taste quality. ↩︎
11.
Among other advantages of having an integrated model: our model itself already bakes in most of the various adjustments that Davidson and Houlden did ad-hoc to their estimate of r, and we can generally ensure reasonable starting conditions (as opposed to Davidson and Houlden’s gradual boost). ↩︎
12.
_Our model operationalizes AC as follows:_ An AC, if dropped into present day, would be as productive on their own as only human coders with no AIs. That is, you could remove all human coders from the AGI project and it would go as fast as if there were only human coders. The project can use 5% of their compute supply to run ACs. ↩︎
13.
See especially this Anthropic survey of researchers claiming >100% productivity improvements, but also this METR uplift study which found that people systematically overestimate the amount of uplift they were getting from AI assistance. ↩︎
14.
That is, if we think that eventually there will be an AI system which outperforms humans at all horizon lengths, then that means the trend must shoot to infinity in finite time. ↩︎
15.
That is, the part of our model that deals with AI timelines, i.e. the length of the period leading up to the “automated coder” milestone, centrally involves the METR trend. After that milestone is reached, horizon length continues to increase but isn’t directly relevant to the results. The results are instead driven by increases in automated research taste and coding automation efficiency. ↩︎
16.
_Our model operationalizes SAR as follows_ : if dropped into an AGI project in present day, a SAR would be as good at research taste as if there were only human researchers, who were each made as skilled as the top researcher. ↩︎
17.
_What do we mean when we say that the gap between a top human researcher and SIAR is 2x greater than that between the median and top human researcher?_ We mean the following. First, let’s define a transformation between AIs’ capability level b and a number of SDs relative to the median as: ↩︎
18.
_Our model operationalizes TED-AI as follows:_ A TED-AI is an AI system that could, if dropped into the present day & given the resources of a large tech company & three months to prep, fully automate 95% of remote work jobs in the US. It need not be able to do all 95% at the same time (perhaps there isn't enough compute to run enough copies of the TED-AI for that), but it needs to be able to do any 10% of them using only 50% of the US's AI-relevant compute. ↩︎
19.
_Our model operationalizes ASI as follows:_ An ASI would, if dropped into present day & given the resources of a large tech company & three months to prep, be able to fully automate 95% of remote work jobs in the US to the level where it is qualitatively 2x as much above the best human as the best human is above the median professional. Also, here we define “the median professional” not as the actual median professional but rather as what the the median professional would be, if everyone who took the SATs was professionally trained to do the task. (We standardize the population that is trained to do the task because otherwise the ASI requirement might be quite different depending on the population size and competence levels of the profession. See above regarding how we define the 2x gap.) ↩︎
20.
Spot-checking in our model: Serial coding labor multiplier is basically the square root of parallel coding labor multiplier, and so when I look at my default parameter settings at the point where serial coding labor multiplier is ~10x (May 2030) the AIs have research taste equivalent to the median AI company researcher. Sounds about right to me. ↩︎
21.
I’ve talked about this elsewhere but I generally think that if you don’t like using a superexponential and insist on an exponential, you need to come up with a different interpretation of what it means for a model to have horizon length X, other than the natural one (“A model has horizon length X iff you are better off hiring a human for coding tasks that take humans much longer than X, but better off using the model for coding tasks that take humans much less than X.”) Because on that interpretation, an exponential trend would _never_ get to a model which outperforms humans at coding tasks of any length. But we do think that eventually there will be a model which outperforms humans at tasks of any length. In other words, on the natural interpretation the trend seems likely to go to infinity in finite time eventually. You can try to model that either as a smooth superexponential, or as a discontinuous phase shift… even in the latter case though, you probably should have uncertainty over when the discontinuity happens, such that the probability of it happening by time t increases fairly smoothly with t. ↩︎
22.
For example, I want to think more about serial speed bottlenecks. The model currently assumes experiment compute will be the bottleneck. I also want to think more about the software-only-singularity conditions and whether we are missing something there, and square this with soft upper bounds such as “just do human uploads.” ↩︎
23.
Note that with the new model, we’ve moved toward using _Automated Coder (AC)_ as the headline coding automation milestone, which has a weaker efficiency requirement. ↩︎
24.
That said, we note that the benchmarks and gaps version had longer median SC timelines (Dec 2028). And Eli’s all-things-considered SC median was further still in 2030, though Daniel’s was 2028. ↩︎
25.
That said, we still think that the AI Futures Model gives too low a probability of <10 year takeoffs, because we are not modeling growth in compute due to hardware R&D automation, hardware production automation, or broad economic automation. ↩︎
26.
As discussed here, the AI 2027 model set _r_ =2.77 and 1.56 at different points. _b_ =2^(1/r-1), so _b_ =0.64 to 0.78. ↩︎
27.
See here for a more thorough explanation of how _b_ is calculated from our new model’s parameters. ↩︎
28.
2^((1/2)-1) gives roughly 0.7. See how we got these numbers here. ↩︎
29.
2^((0.315/0.248)-1). See the justification for this formula on our website. ↩︎
30.
Note that the minimum b in our model is 0.5. This is a limitation, but in practice, we can still get very fast takeoffs. For example, if b were 0.5 and didn’t change over time, this would lead to a finite-time singularity in 2 times longer than the initial uplift doubling time. ↩︎
31.
This could also be influenced by the uplifts being different for different milestones, or other factors. Unfortunately we haven’t had a chance to do a deep investigation, but a shallow investigation pointed toward compute increases being the primary factor. ↩︎
1.
**^**
Eg looking at transcripts to determine where humans are spending their time when they give Cursor tasks of a certain length
AI1Frontpage
# 134
# Ω 46
AI Futures Timelines and Takeoff Model: Dec 2025 Update
21Thomas Kwa
8bhalstead
4elifland
4habryka
2Daniel Kokotajlo
0Noosphere89
20Thomas Kwa
7elifland
3Oliver Sourbut
3Oliver Sourbut
18MP
7elifland
3Noosphere89
1MP
3Petropolitan
16Oliver Sourbut
15Fabien Roger
9ryan_greenblatt
3Daniel Kokotajlo
3Fabien Roger
2Oliver Sourbut
2Sheikh Abdur Raheem Ali
13enterthewoods
5elifland
5enterthewoods
8Thomas Larsen
9elifland
2Josh You
2Daniel Kokotajlo
4AnthonyC
New Comment
30 comments, sorted by 
top scoring
Click to highlight new comments since: Today at 8:43 PM
[-]Thomas Kwa13dΩ11213
Thoughts in no particular order:
  * Kudos for what seems to be lots of thoughtful work incorporated into this model.
  * There are a lot of parameters. Maybe this is necessary but it's a bit overwhelming and requires me to trust whoever estimated the parameters, as well as the modeling choices.
  * I couldn't find a block of equations that represents the whole model, or an index of variables in one place, and it's difficult to go between math and exposition especially when the equations are hidden in dropdowns, so I still feel like I don't have a good picture. I had Claude do this and read through it, and it looks reasonable but some parts are still not defined in Claude's summary, I think because the whole page is rendered in javascript and it couldn't access it. I would love to visit the AI Futures office again to understand the model better.
  * I find the use of time horizon as such a crucial intermediate variable confusing and am scared of potential assumptions around it.
    * Time horizon is underdefined on years long tasks. I know I talked to the AI Futures team about what you now wrote up as METR-HRS-Extended to operationalize it, but it's unclear what a 3-year time horizon really means (when extrapolating the trend with superexponential adjustment) given factors like the increased number of details and interaction with longer tasks. Does the trend mean that in X years, an AI will competently substitute for a human for a 3-year long project with the same level of feedback from a manager, or be able to do the human's job with less feedback?
    * The function that relates time horizon and research speedup in the real world is very unclear. I'm trying to collect data on this and it's highly nontrivial to model and interpret[[1]](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<#fnt1ajxwz0ohe>) so I'm skeptical of any use of the time horizon trend to predict uplift that doesn't either have a simple, robust model or something validated by experiment.
    * The description implies that time horizon is only used in the first phase (pre-AC), but I don't understand how it's used. Humans and AIs will probably go from their current state to perfectly substitutable in some kind of continuous progression and I couldn't find the formula for this. Also when I changed the "How much easier/harder each coding time horizon doubling gets" parameter by small amounts, the forecasted time from AC to ASI changes significantly (2.7 years at 0.90, over 4 years for 1.00), so it looks like stages 2 and 3 are affected as well.
    * It seems to me like a time horizon of 3 years or 125 years is complete overkill for automation of _enough_ coding for the bottleneck to shift to experiments and research taste.
  * Why not assume that compute is allocated optimally between experiment, inference, etc. rather than assuming things about the behavior of AI companies?
  * I wish the interface were faster to update, closer to 100ms than 1s to update, but this isn't a big deal. I can believe it's hard to speed up code that integrates these differential equations many times per user interaction.

  1. **^**
Eg looking at transcripts to determine where humans are spending their time when they give Cursor tasks of a certain length

Reply
11
[-]bhalstead12d82
Thanks!
> There are a lot of parameters. Maybe this is necessary but it's a bit overwhelming and requires me to trust whoever estimated the parameters, as well as the modeling choices.
Yep. If you haven't seen already, we have a basic sensitivity analysis here. Some of the parameters matter much more than others. The five main sliders on the webpage correspond to the most impactful ones (for timelines as well as takeoff.) There are also some checkboxes in the Advanced Parameters section that let you disable certain features to simplify the model. 
**Regarding form factor / conciseness:** thanks for the feedback! Seems like people have widely varying opinions here. Would you prefer a form factor like this to what we currently have? Would you prefer a Big Table of every equation plus a Big Table with every symbol to what we currently have? (by the way, I can't actually see the file Claude made --- maybe it would work if you shared the artifact rather than the conversation?) 
**Relating time horizon to coding automation speedup:** The only purpose of the time horizon in the model is to forecast the effective compute (which we would like to interpret as abstract "capability" or ECI or something) required for the Automated Coder milestone. You could do this in other ways, e.g. using Bio Anchors. (In fact, I would like to do it in other ways for more robustness, but sadly we didn't have time before the launch.) We roll out the "human-only" trajectory to translate the current (release date, time horizon) trend into an (effective compute, time horizon) trend, then extrapolate that until the horizon reaches the AC requirement. This tells you the effective compute required for Automated Coder. This is then used to fit a separate automation schedule (which tells you the "fraction of coding tasks automated" at each effective compute level) which gets anchored to 100% at the AC effective compute value. (Another degree of freedom is pinned down to match our estimate of today's coding uplift). This automation fraction is used in a task-based CES model to compute the aggregate coding labor at each time, which is a pretty standard technique in economics for modeling automation, but not necessarily good. We think a more gears-level model of the delegation / reviewing / etc process of agentic coding would be more accurate, but again didn't come up with a fully-formed one in time. I'm curious to hear more about the data you're collecting on this!Seems useful to talk to us in person about interpretations of time horizon / why 130 years is maybe reasonable. Eli's rationale for that estimate is written up here. 
> Why not assume that compute is allocated optimally between experiment, inference, etc. rather than assuming things about the behavior of AI companies?
As with many things in this project, we wish we had more time to look into it, but didn't prioritize it because we thought it would affect the results less than other things. When I briefly thought about this in the middle of the project, I remember getting confused about what "optimal" should mean. It also seems like it might increase the complexity of solving the model (it could increase the dimension of the system of differential equations, depending on how it's done.)
Messy digression on ways one might do this
For example, how do you decide how to allocate compute between experiments and training? If your goal is "maximize the effective compute of your frontier model at the end of the year", the optimal policy is to spend all of your compute on experiments now, then at some specific time switch to all-training. But setting a schedule of "deadlines" like this seems unnatural.You could also imagine that at each point in time, the lab is spending an amortized "training budget" in H100e equaling (size of actual frontier training system) x (fraction of each year during which it's utilized for training production models)or(company H100e) x (fraction of H100e in frontier training system) x (fraction of each year when it's utilized) which is, assuming one frontier-scale production training run per year (which is maybe reasonable??):(company H100e) x (fraction of H100e in frontier training system) x (min{1, training run length / 1 year}).Jointly optimizing the FTS fraction, the training run length, and experiment compute seems like a bit of a mess, since the software efficiency that matters is probably the software efficiency at the beginning of the run, which you already decided at a previous timestep... possibly there's a nice solution to this. Might think about it more later today. One thing I agree would be easy and I should probably do is plot the implied MRTS between experiment compute and automation compute over time, i.e. the number of experiment H100e you'd need to gain such that simultaneously losing a single automation H100e doesn't affect the software efficiency growth rate (or equivalently research effort). Theoretically this should always be 1. I bet it isn't though.
> I wish the interface were faster to update, closer to 100ms than 1s to update, but this isn't a big deal. I can believe it's hard to speed up code that integrates these differential equations many times per user interaction.
Very interesting to hear! One main obstacle is that the model is being solved on the server rather than the client, so getting to 100ms is hard. There's also a tradeoff with time resolution (with very fast takeoffs, the graphs already look a bit piecewise linear.) But I think there is definitely room for optimization in the solver. 
Reply
[-]elifland12dΩ340
> Also when I changed the "How much easier/harder each coding time horizon doubling gets" parameter by small amounts, the forecasted time from AC to ASI changes significantly (2.7 years at 0.90, over 4 years for 1.00), so it looks like stages 2 and 3 are affected as well.
I'd guess that this is only because compute growth (and human labor growth, but that doesn't matter as much) at that point is slower during takeoff if takeoff starts later.
Let's test this, this theory would predict that whatever time horizon growth parameter I changed, would result in the same takeoff if it ends up starting at the same time:
  1. From the starting state, if I raise "How much easier/harder..." to 0.99, AC happens in 1/2040, and ASI happens in 3/2044 (so 4 years 2 months, replicating you)
  2. If I instead raise present doubling time ("How long it...") to 9.5 months, then AC happens in 12/2039, and ASI happens in 2/2044 (same speed as in (1))
  3. I can't get AC at that time by only raising AC time horizon requirement, but if I raise it to the max, then raise "How much easier/harder..."to 0.95, I get pretty close: AC at Jul 2038, and ASI at Aug 2042. Barely under 4 year takeoff. If I also raise present doubling time to 6 months, then I get 8/2040 to 11/2044 takeoff, 4 year 3 month takeoff.

~~Ok, looks like I was right. I'm pretty sure that these do affect takeoff, but only by changing the starting date.~~
Edit: actually sorry these can also affect takeoff via the coding automation task efficiencies when reaching AC / start of takeoff, because if the effective compute requirement is different then the logistic curve has a lower slope, not just shifted over to the right. My guess is that the compute growth is having a larger impact, but we'd have to do a bit more work to check (either way each time horizon growth parameter would have the same effect if it reuslted in AC happening at the same time, because all the parameters do is set the effective compute requirement for AC).
Reply
[-]habryka12dΩ440
> I wish the interface were faster to update, closer to 100ms than 1s to update, but this isn't a big deal. I can believe it's hard to speed up code that integrates these differential equations many times per user interaction.
Yeah, currently the rollout takes around a second, and is happening on a remote python server, because JS isn't great for this kind of work. We already tried pretty hard to make it faster, though I am sure there are ways to get it to become actually fully responsive, but it might be many engineering hours. 
Reply
[-]Daniel Kokotajlo12dΩ220
(adding to what bhalstead said: You are welcome to stop by our office sometime to chat about it, we'd love to discuss!)Personally I agree that 125 years is complete overkill for automation of enough coding for the bottleneck to shift to experiments and research taste. That's a big part of why my parameter is set lower. However I want to think about this more. You can find more of my thoughts here.
Reply
[-]Noosphere8912d0-3
> It seems to me like a time horizon of 3 years or 125 years is complete overkill for automation of enough coding for the bottleneck to shift to experiments and research taste.
My small comment on this is that this is mostly fine if you take the worldview that tacit knowledge matters and the inflated time horizon is to make sure that tacit knowledge needs are adequately modeled here.
Steve Newman has a good discussion on this here.
Reply
[-]Thomas Kwa12dΩ7204
After @Daniel Kokotajlo invited me to the AI Futures office I ended up talking to Eli and Alex for about an hour, and feel like I have a decent understanding of the model:
### Summary of the AI Futures Model
  * Compute and effective compute
    * Actual compute C(t) is stock of compute at time t
    * Effective compute E(t):=C(t)⋅software efficiency is used as the main measure of AI capabilities. It is defined as the "amount of training compute we’d need to train models as performant as the frontier models at time t using the training process of the present-day".
    * Compute is allocated as fixed percentages between training, experiments, and automated coders
  * Effective labor
    * The % of tasks automatable is a logistic function of log effective compute E(t)
    * Once a task can be automated, it will still get more efficient over time by a multiplier ηi(t)
      * ηi(t) is zero for non-automated tasks. When effective compute reaches the level Ei required to automate it, it increases as a power law ηinit ⋅(E(t)Ei)ηslope .
    * Human coding labor LC,H(t) and automation compute Caut,i are optimally allocated between tasks
    * Overall coding labor for task i is the sum of human and AI labor Gi=LC,H,i+ηi⋅Caut,i
    * Aggregate coding labor LC(t) is CES between the labor applied to all different tasks, with low substitutability ρc=−2 by default, meaning tasks only substitute slightly for each other
      * Lc(t)=(∫10Gi(t)ρcdi)1/ρc=(∫10(Lc,H,i(t)+ηi(t)⋅Caut,i(t))ρcdi)1/ρc.
    * Finally, serial coding labor ~LC(t)=LC(t)λ, indicating diminishing returns of about λ=0.5 to adding more labor in parallel
  * “Experiment throughput” X(t) is CES between serial coding labor and experiment compute
    * X(t)=(α~Cxpm(t)ρx+(1−α)~LC(t)ρx)1/ρx,0<α<1,ρx<0
    * Labor and compute are slight complements (median estimate ρx=−0.155)
    * There are also diminishing returns to compute, with ~Cxpm=Cζxpm where ζ=0.65 by default
  * Research taste T(t)
    * Human research taste is lognormally distributed with median researchers defined as 1x taste and 99.9th percentile (+3.1SD) researchers assumed to have 3.70x research taste
    * An Automated Coder–level AI has research taste TAC
    * AI research taste increases as a power law in effective compute (AI “research taste IQ” is Trate⋅logE(t)+const standard deviations above the human median, which is then passed through an exponential to get research taste)
    * AIs replace whatever humans they’re better than. The aggregate research taste of the company is the mean of all remaining researchers. This means it initially increases slowly as AIs replace the worst researchers, then speeds up as everyone starts using the AIs’ research taste which keeps improving.
  * Research effort RE(t) = research taste * experiment throughput
  * Then software efficiency S(t) follows the Jones model ˙S=S1−βRE(t)
    * β is how much harder AI R&D gets as software efficiency advances
  * Finally this feeds back into effective compute E(t)=Ctrain(t)S(t)
    * A taste-only singularity happens when m>β, where m = doublings of research taste per doubling in effective compute. This would cause improvements to go faster and faster until approaching physical limits. Eli's parameter choices give 38% chance of taste-only singularity, but many of the non-singularity samples still get to ASI quickly, with the 50th percentile sample getting from AC to ASI in 5 years.
    * For various reasons Eli and Daniel's all-things-considered views have harder takeoff than the model predicts, with Eli's median for AC -> ASI 2 years, and Daniel's median 1.5 years.

### Notes on Sensitivity analysis
  * Time to AC is very sensitive to how superexponential time horizon growth is, and also to
    * The present doubling time
    * Time horizon for automated coder
  * Time from AC to ASI is very sensitive to the “automated research taste slope” Trate: how much “research IQ” AIs gain per doubling of effective training compute. But many other factors could slow down the AC-to-ASI duration to >6.5 years:
    * Median-to-top-human jumps above SAR needed to reach TED-AI
    * The software efficiency growth rate in 2024
    * Median to 99.9th% human research taste multiplier
    * Slowdown from 10x less experiment compute
    * Research progress rate in the limit of infinite coding labor: mostly because it’s highly uncertain (their 90% CI is 2.0-201)
    * Automated research taste of an AC

### Biggest uncertainties to track
(not necessarily that I disagree, just need to think about it more)
  * Effective compute vs time horizon: how do all the assumptions look when we eliminate time horizon from the model and use other methods to model effective compute growth? I’m sketched out by the huge error bars on time horizon superexpontiality → time to AC
  * Ryan thinks >70% of code at Anthropic was written by AIs already in October 2025 but it’s mostly low-value code. Code varies dramatically in value, and AIs can expand the number and type of low-value tasks done rather than just substituting for humans. This may be a separate effect from AIs doing extra work on tasks that can be automated, which is not tracked by the model.
    * It might be that coding ability and research taste are two ends of a continuous spectrum from small-scale to large-scale tasks.
  * Research taste:
    * Someone really needs to do experiments on this, it’s possible now. David Rein and I are actively thinking about it
    * Is human research taste modeled correctly? Eg it seems likely to me that the 0.3% of top humans add more than 0.3%*3.7x to the “aggregate research taste” of a lab because they can set research directions. There are maybe more faithful ways to model it; all the ones Eli mentioned seemed far more complicated.
    * Is modeling AI research taste as exponential in human standard deviations valid? I have no idea whether someone 9 standard deviations above the human median would be able to find 3.7^(9/3) = 50x better research ideas or not
  * Is CES valid for experiment throughput at these extreme values of labor and compute? It seems like a superhuman AI researcher might learn to run experiments more efficiently, decreasing the compute required for each experiment. The estimates for experiment throughput parameters were all about _humans_ getting 10x compute, infinite labor, etc. Or, they could coordinate better (especially with all the human ex-coders to help them), and decrease the parallelization penalties for labor and/or compute. I’m not sure if this would be different from adjusting research taste.

Reply
32
[-]elifland12d*Ω570
Thanks for writing this up! Excited about research taste experiments.
> Is human research taste modeled correctly? Eg it seems likely to me that the 0.3% of top humans add more than 0.3%*3.7x to the “aggregate research taste” of a lab because they can set research directions. There are maybe more faithful ways to model it; all the ones Eli mentioned seemed far more complicated.
A minimal change would be to change the aggregation from mean to something else, we were going to do this but didn't get to it in time. But yeah to do it more faithfully I think would be pretty complicated because you have to model experiment compute budgets for each human/AI. Note also that we aren't really modeling human/AI taste complementarity.
> Or, they could coordinate better (especially with all the human ex-coders to help them), and decrease the parallelization penalties for labor and/or compute
Agree that ideally there would at least be different penalties for AIs vs. humans doing the labor.
> Is modeling AI research taste as exponential in human standard deviations valid? I have no idea whether someone 9 standard deviations above the human median would be able to find 3.7^(9/3) = 50x better research ideas or not.
Note that because of limits (which weren't in your summary) the model is in practice subexponential, but exponential is generally a good approximation for the model around the human range. See here (4.2.2) for an explanation of taste limits.
Regarding whether it's a good approximation in the human range, we have some n=12 survey results on this here, obviously take with a huge grain of salt, but extracted from these results the ratio of (taste per SD between the 90th percentile and top researchers) and (taste per SD between 50th percentile and top) appears to be fairly close to 1: 1.01 median if assuming a population of 1000 researchers, and 0.95 median if assuming a population of 100.
Reply
[-]Oliver Sourbut11d30
I've a simple model of research taste:
  * Research is exploration: trying stuff to gain information about what happens and what works
  * You're planning experiments, the unit of that exploration
  * This planning benefits from heuristics that generate, refine, and select better experiment plans: that's taste 
    * (As well as these heuristics, you can just _plan for (effectively) longer_ if you have more thinkspeed, but I tentatively believe that falls off sharply per unit, until you get more feedback from reality, even when it's serial thinkspeed)
  * How do you get these heuristics? By necessity, they're partially-generalising models based on experience _of experiments_
    * (That experience can be indirect, in the form of textbooks or expert interviews etc.)
    * (But the key point is that taste isn't just a generic capacity or quantity you have; it comes from looking at the world, specifically getting a feel for high value-of-information interactions)
  * So **experimental throughput is crucial** , as is **sample efficiency** (at improving your taste models)
  * Taste is a stock; it depreciates due to movement of the frontier of the known 
    * You learn stuff from your experiments, you enter (more or less) different regimes, your heuristics are that bit further from their solid base of generalisation
    * How fast this deprecation happens is therefore of great interest i.e. how generalising is research taste in a given domain?
    * (This deprecation also means that the one-time boost to taste stock by slurping up all textbooks and expert interviews etc. is limited, but it's not clear _how_ limited)

Reply
[-]Oliver Sourbut11d30
There are a bunch of parameters that look important on this view:
  * how 'far' does taste generalise (in the given domain) 
    * or equivalently (and perhaps easier to instrumentalise and estimate?) how fast does it depreciate as the frontier moves?
  * how fast does the return to extra reasoning for experiment design diminish?
  * what are sample efficiency scaling laws like? (Does this change for finetuning and in-context sample efficiency and the like?)
  * do returns to research on effective compute look different form returns to research on sample efficiency? 
    * I expect yes, in part because effective compute improvements are a bit more straightforward to verify

Reply
[-]MP10dΩ5182
Thank you for the effort. Big fan of the authors.
The authors don't seem to address the possibility that we are seeing a temporary acceleration of AI, because the labs are ramping methods that are much more expensive to scale, but they are doing so from very low baselines.
Here some evidence for you.
1- The acceleration in ECI precedes coding helping researchers in at least 18 months. Based on my anecdotes, I doubt any researcher at an AI lab is being accelerated by AI since they got access to models like 4.5 Sonnet and GPT-5-Codex. Epoch says: "AI capabilities accelerated in 2024! According to our Epoch Capabilities Index, frontier model improvement nearly doubled, from ~8 points/year to ~15 points/year." **I don't think there's any reason to believe that AI-aided R &D acceleration has happened in any meaningful way**, other than maybe Sholto's comment.
2- One place where has been an acceleration is on my spending on AI. I am now spending more than one thousand dollars in tokens and the marginal task of my job I am automating with AI costs what I used to pay for AI during an entire month. **Toby Ord argues that the costs of AI are increasing exponentially** : "the hourly costs for some models are now close to human costs." While the evidence is small and we need further work, if each jump makes the marginal task exponentially more expensive, but for a fixed level of intelligence, we get prices 90% cheaper per year, one could imagine a point where we achieve the AGI at 2028, but only can deploy it economically in 2030. And a world where we achieve the Automated Coder in 2031, but only can deploy it economically in 2035.
3- Despite the METR and ECI indexes of capabilities per unit of time following an exponential with even an acceleration, **the underlying trends have changed massively**. a- Pretraining scaling has slowed down massively since the GPT-4.5 debacle. b- Massive efforts have been done to create human cured data around the matters we care about. SemiAnalysis say the labs are spending single-digits billions on human generated data. Beren argues most algorithimic progress is data progress. Obviously, replacing the corpus of text from random dudes debating in a 2007 forum to all the intermediate steps of a math proof by a math PhD improves the models. Obviously, this can't scale and is an one-off improvement. b- Inference-time scaling has been improving the models considerably. To the point, I consider OpenAI models like GPT-5.2-Codex-High unusable, given how slow they are. Not only that, but gains from inference-time scaling must be paid every time they are executed. I don't think we can continue to scale inference time compute into the back-half of the decade. c- Toby Ord also argues that RL is in on the order of 1,000,000x less compute efficient than pre-training. He says "I estimate that at the time of writing (Oct 2025), we’ve already seen something like a 1,000,000x scale-up in RL training and it required ≤2x the total training cost. But the next 1,000,000x scale-up would require 1,000,000x the total training cost, which is not possible in the foreseeable future." Regardless of the level, I feel anyone paying attention feels the same way. Ilya argues that RL is learning from a straw. 
3a- Google DeepMind Co-founder and CEO, Nobel Prize winner, Demis Hassabis said he is spending _most_ of his time on world models. Facebook AI Research co-founder Yann LeCunn says "LLMs are a dead" and is working on world models. I feel that the "straight-line on charts" crowd, which I am definitely part of, ignore the important aspects of empiricism in the construction of human knowledge. We won't create a LLM with a one-month time horizon and it will reason from first principles how to cure cancer. That's exactly the opposite lesson from Samuel Albaine's Compute Theory of Everything. 
4- The authors don't address that they are making a somewhat unverifiable prediction. The largest tasks inside the METR are on the order of 16 hours. I'd argue that the complexity of benchmarking translates to the complexity of improving the models themselves. 
4a- I can imagine doing RLVR with endless goals, like Stockfish is always getting better in chess. Maybe we can have LLMs that are ever increasing better in creating better matrix factorization algorithms. I struggle to find which types of such algos we could have where overshooting human capability would be insanely singularity good.
4b- RL doesn't seem to generalize. My market Will a large language model beat a super grandmaster playing chess by EOY 2028? is at 44% and the trend is down. Maxin Saplin's leaderboard of LLM chess has Gemini 3 Pro merely at 1033 rating, vs 1500 for a "class C player". While I have no doubt that if the labs wanted, they could RLVR chess into their LLMs, I think chess is a good example that you can't do insane amounts of RL in one direction and expect good things in other directions. 
5- I'd argue "significantly more important than the internet" singularity requires solving one or more of continual learning and simulation (a.k.a. world models). Computers will only get better in matters that involve the real world quickly if they aren't bounded by the real world. 
All that said, I confess the straight lines on a chart are immensely persuasive and hard to not extrapolate for many years through the Lindy Effect.
Reply
1
[-]elifland7dΩ370
Thanks for the comments! Besides the below, I'm curious what your overall views are. What does your distribution for AC look like?
> The authors don't seem to address the possibility that we are seeing a temporary acceleration of AI, because the labs are ramping methods that are much more expensive to scale, but they are doing so from very low baselines.
I think this is basically addressed in our uncertainty over the present doubling time, at least that's how I'd think of it for myself. Note that my median present doubling time estimate of 5.5 months is slower than the potentially accelerated recent time horizon trend.
> **I don't think there's any reason to believe that AI-aided R &D acceleration has happened in any meaningful way**,
Our model reflects that, with my median parameters the current software R&D upflit is 1.1x.
> 2- One place where has been an acceleration is on my spending on AI. I am now spending more than one thousand dollars in tokens and the marginal task of my job I am automating with AI costs what I used to pay for AI during an entire month. **Toby Ord argues that the costs of AI are increasing exponentially** : "the hourly costs for some models are now close to human costs." While the evidence is small and we need further work, if each jump makes the marginal task exponentially more expensive, but for a fixed level of intelligence, we get prices 90% cheaper per year, one could imagine a point where we achieve the AGI at 2028, but only can deploy it economically in 2030. And a world where we achieve the Automated Coder in 2031, but only can deploy it economically in 2035.
Our Automated Coder has efficiency definitions built in, so you wouldn't put it that way, you'd instead say you get an Automated Coder in 2035 and a very expensive replication of AC abilities in 2031. I personally think that a large majority of the relevant recent gains have not come from inference scaling, but if I did think that a lot of it had been, I would adjust my present doubling time to be slower.
Here's **some portions of a rough Slack message** I wrote recently on this topic:
> Let me try... a concrete case study: let's compare GPT-4 and GPT-5 and long-horizon coding (if we did GPT-3 vs. GPT-4 it would be even more obvious, but perhaps better to discuss a jump that's more recent).Our model says that this is a 10,000x increase in effective compute, i.e. 4 OOMs (it seems more relevant to discuss something like effective compute, ECI, etc. rather than pure compute scaling, because pure compute scaling isn't what happens in practice). Now your numbers (as far as I understand) say that we could achieve the same gains with 6 OOMs of inference compute if this all came from pretraining, or 2 OOMs of inference compute if this all came from RL [note for LW: this was responding to the exchange rates proposed in <https://www.tobyord.com/writing/how-well-does-rl-scale>]. From <https://evaluations.metr.org/gpt-5-report/>, I'm attaching what they say for the curve of tokens->performance on METR's time horizon suite. 
> 
> We can't even see GPT-4 here, but GPT-4o for example is clearly basically asymptoting at something like 10 minute time horizons. Meanwhile GPT-5 is above 2 hours at max tokens. If we look at 10 minute time horizons, then according to this graph GPT-5 is a bit _more_ expensive, though iirc the graph overrepresents GPT-5 costs (e.g. it should not be near o3's costs). But if we look at 2 hour horizons (or even like 20+ mins), it's essentially an infinite cost improvement over GPT-4o, much less GPT-4 (this is a bit oversimplified because models obviously have probabilistic success rates at each horizon, but I don't think it changes the basic takeaway).
> So stepping back, we see that how we compare scaling effective compute / ECI / "years of recent progress" (pick your favorite) to inference scaling just changes a ton based on what difficulty of task you're looking at, but if it's more difficult (and if you are looking at a larger effective compute difference) then you basically can't match it with any practically achievable amounts of inference scaling. And imo those are the tasks we care the most about! So I find these inference scaling comparison numbers interesting and informative for some questions, but not as relevant to the overall picture relative to other capability forecasting lenses.
> Btw also attaching a pic from <https://www.anthropic.com/news/claude-opus-4-5> comparing SWEBench-Verified on Sonnet and Opus 4.5. Obviously just one data point but I found it interesting on just a short time frame (~2 months) Anthropic saw 5x token efficiency improvement at high levels of SWEBench-Verified performance (Opus 4.5 is about 1-1.7x as expensive per token), and that's not even looking at the highest levels, I assume the multiplier would be much higher if you tried to scaling Sonnet to reach Opus's high performance.
> 
[end Slack message]
Furthermore, it seems that once capabilities can be reached very expensively, they pretty reliably get cheaper very quickly. See here for my research into this or just skip to Epoch's data which I used as input to my parameter esitmate; happy to answer questions, sorry that my explanation is pretty rough.
> 3- Despite the METR and ECI indexes of capabilities per unit of time following an exponential with even an acceleration, **the underlying trends have changed massively**. a- Pretraining scaling has slowed down massively since the GPT-4.5 debacle. b- Massive efforts have been done to create human cured data around the matters we care about. SemiAnalysis say the labs are spending single-digits billions on human generated data. Beren argues most algorithimic progress is data progress. Obviously, replacing the corpus of text from random dudes debating in a 2007 forum to all the intermediate steps of a math proof by a math PhD improves the models. Obviously, this can't scale and is an one-off improvement. b- Inference-time scaling has been improving the models considerably. To the point, I consider OpenAI models like GPT-5.2-Codex-High unusable, given how slow they are. Not only that, but gains from inference-time scaling must be paid every time they are executed. I don't think we can continue to scale inference time compute into the back-half of the decade. c- Toby Ord also argues that RL is in on the order of 1,000,000x less compute efficient than pre-training. He says "I estimate that at the time of writing (Oct 2025), we’ve already seen something like a 1,000,000x scale-up in RL training and it required ≤2x the total training cost. But the next 1,000,000x scale-up would require 1,000,000x the total training cost, which is not possible in the foreseeable future." Regardless of the level, I feel anyone paying attention feels the same way. Ilya argues that RL is learning from a straw. 
I think I already addressed at least part of this in my answer to (2).
> 4- The authors don't address that they are making a somewhat unverifiable prediction. The largest tasks inside the METR are on the order of 16 hours. I'd argue that the complexity of benchmarking translates to the complexity of improving the models themselves. 
I don't understand this. What exactly do you want us to address? Why should we adjust our predictions because of this? We do explicitly say we are assuming a hypothetical "METR-HRS-Extended" benchmark in our explanation. Ok maybe you are saying that it will be hard to create long-horizon tasks which will slow down the trend. I would say that I adjust for this when my make my all-things-considered AC prediction longer due to the potential for data bottlenecks, and also to some extent by making the doubling difficulty growth factor higher than it otherwise would be.
> All that said, I confess the straight lines on a chart are immensely persuasive and hard to not extrapolate for many years through the Lindy Effect.
Yeah for the parts I didn't explicitly respond to, my response is mainly that it seems like this sort of inside view reasoning is valuable but overall I give more weight to trend extrapolation, and historically simple trend extrapolations like "when will we have the same ops/second as the human brain" have performed pretty well, as we discuss in our blog post.
Reply
[-]Noosphere899d30
> a- Pretraining scaling has slowed down massively since the GPT-4.5 debacle.
This is the one element of the comment that doesn't really stand up, because new data centers that are much larger are being constructed, and the GPT-4.5 debacle was near entirely because we compared GPT 4.5 to o3 with RL, as well as compute being scaled 10x instead of 100x.
Pre-training is still going strong, it's just rested a bit due to the crazy RL scaleup, and it will come back in importance (absent continual learning).
This implies that their trend up to 2030 is likely accurate, but post-2030 absent new paradigms will look a lot different than the median scenario in their model.
Reply
[-]MP9d10
GPT-4 was pre-trained in 2022. GPT-4o was pre-trained in 2024. Since then, models likely have the same size. Clearly something is happening that no one wants to spend 100x more in a pre-train run. Likely because you need high-qualitt non-synthetic data.
Reply
[-]Petropolitan9d*30
Thanks, a very interesting post, sounds quite convincing!
> Beren argues most algorithimic progress is data progress.
Checking the source, he wrote:
> A GPT4 scale model trained on the dataset of GPT3 would be substantially worse across all benchmarks, even if we somehow replicated the GPT3-dataset to be the scale of GPT4s dataset.
My pet theory is that's what basically happened with Llama 4 Behemoth. Unfortunately, I don't know how to verify or falsify it.
However, there is a counterargument to be made to Beren's thesis: namely, the NanoGPT speedrun. Granted, it only started in May 2024, but as noted by Tamay Besiroglu last February (he in turn expands on ideas and methods from a 2023 Epoch AI paper on game speedruns), the speed increases as a power law with the number of tries (not the calendar time!).
The trend line is still mostly the same as in his tweet, although I ran a QLR/sup-Wald test for structural breaks to be sure, and detected two slightly different periods:
  * one before the introduction of FlexAttention in November 2024, which caused a statistically significant jump down (visible on the chart in the tweet);
  * and another for all of the records since (actually most of the data, 46 points out of 58 as of this writing).

I also redid the analysis for the successive record ratios with the new data, the trend is basically the same despite the noise and scatter but QLR appears to not work properly because of timing rule changes last January.
I would have posted the charts here but don't know how to do that (just I couldn't figure out how to ping Beren!), and one can redo the analysis with an LLM or a coding agent anyway.
These results imply that non-data algorithmic progress for pretraining specifically definitely exists (the speedup is ~25x in under 2 years) but may be a bit slowing down over time.
Reply
[-]Oliver Sourbut11d160
(I forgot that more conversation might happen on a LW crosspost, and I again lament that the internet has yet to develop a unified routing system for same-content-different-edition discourse. Copied comment from a few days ago on substack:)
I really appreciate this (and other recent) transparency. This is much improved since AI 2027.
One area I get confused by (same with Davidson, with whom I've discussed this a bit) is 'research taste'. When you say things like 'better at research taste', and when I look at your model diagram, it seems you're thinking of taste as a generic competence. But what is taste? It's nothing but a _partially-generalising learned heuristic model of experiment value-of-information_. (Said another way, it's a heuristic value function for the 'achieve insight' objective of research).
How do you get such learned models? No other way than by _experimental throughput and observation thereof_ (direct or indirect: can include textbooks or notes and discussions with existing experts)!
See my discussion of research and taste
As such, taste accumulates like a stock, on the basis of experimental throughput and sample efficiency (of the individual or the team) at extracting the relevant updates to VOI model. It 'depreciates' as you go, because the frontier of the known moves, which moves gradually outside the generalising region of the taste heuristic (eventually getting back to naive trial and error), most saliently here with data and model scale, but also in other ways.
This makes sample efficiency (of taste accumulation) and experimental throughput extremely important, central in my view. You might think that expert interviews and reading all the textbooks ever etc provide meaningful jumpstart to the taste _stock_. But they certainly don't help with the flow. So then you need to know how fast it depreciates over the relevant regime.
(Besides pure heuristic improvements, if you think faster, you can also _reason_ your way to somewhat better experiment design, both by naively pumping your taste heuristics for best-of-k, or by combining and iterating on designs. I think this reasoning boost falls off quite sharply, but I'm unsure. See my question on this)
Reply
1
[-]Fabien Roger11dΩ915-4
I think fitting the Metr scaling law with effective compute on the x-axis is slightly wrong. I agree that if people completely stopped investing in AI, or if you got to a point where AI massively sped up progress, the trend would break, but I think that before then, a straight line is a better model than modeling that tries to take into account compute investment slow down or doublings getting easier significantly before automated coders.
My best guess is that if you had done the same exercise with semi-conductors in 1990, you would have made bad predictions. For example, Moore's law doesn't hold that well with semi-conductor log(revenue+investments) on the x-axis (according to this plot generated by Claude).

(I think Metr time horizon might not be the right y-axis, maybe more akin to clock speed than number of transistor on a chip, and AI-speeding-up-AI slightly before AC is important when forecasting AGI, so I don't claim that just saying "the line will be straight" removes the need for some other forms of modeling.)
Reply
[-]ryan_greenblatt10dΩ792
The fit is notably better for "cumulative investment over time". Years still produces a slightly better fit.

I've cut off the fit as of 2010, about when the original version of moore's law stops. If you try to project out after 2010, then I think cumulative investment would do better, but I think only because of investment slowing in response to moore's law dying.
(Doing the fit to a lagged-by-3 years investment series doesn't make any important difference.)
Reply
[-]Daniel Kokotajlo10dΩ230
Interesting, thanks! Why though? Like, if a massive increase or decrease in investment would break the trend, shouldn't a moderate increase or decrease in investment bend the trend?The first graph you share is fascinating to me because normally I'd assume that the wright's law / experience curve for a technology gets harder over time, i.e. you start out with some sort of "N doublings of performance for every doubling of cumulative investment" number that gradually gets smaller over time as you approach limits. But here it seems that N has actually been increasing over time!
Reply
[-]Fabien Roger10dΩ230
My guess of what's going on is that something like "serial progress" (maybe within the industry, maybe also tied with progress in the rest of the world) matters a lot and so the 1st order predictions with calendar time as x axis are often surprisingly good. There are effects in both directions fighting against the straight line (positive and negative feedback loops, some things getting harder over time, and some things getting easier over time), but they usually roughly cancel out unless they are very big.
In the case of semiconductors, one effect that could push progress up is that better semiconductors might help you build better semiconductors (e.g. the design process uses compute-heavy computer-assisted design if I understand correctly)?
Reply
[-]Oliver Sourbut10d21
Although superficially similar, I think these are asking different kinds of question.
Chips are a classic production efficiency 'industrial learning curve'. Wright's law is the generic pattern which often holds there: efficiency gains per order of magnitude 'units produced'. As it happens, people have produced exponentially many chips over time, so you also get a smooth line if you plot against time: that's Moore's law.
We might expect similar learning curve patterns to hold for something like 'cost per token' vs 'tokens served'. I'm not aware of definitive public data on those, but superficially the pricing looks to support that. (Incidentally this is another reason I think 'experimental throughput', including serving at scale, is crucial to compute efficiency gains.)
In contrast, time horizons are more of a 'scaling law' question, where something like ('effective') input scale is the right kind of independent variable to track.
Reply
[-]Sheikh Abdur Raheem Ali11d20
I read an article about the history of extreme ultraviolet lithography (http://dx.doi.org/10.1116/1.2127950, the full pdf is on sci-hub) which says that soft x-ray reduction lithography using multilayer-coated schwertzchild optics was demonstrated in 1986.
3 nm process nodes have a contacted gate pitch of 48 nanometers, and a tightest metal pitch of 24 nanometers, so a laser with wavelength near 13.5 nm is needed to etch the circuits onto the chip dies with sufficient precision.
Of course, there were many practical engineering challenges with getting this concept to work at scale (there is a video by veritasium which discusses this in more detail), and I think very few people making compute forecasts in 1990 would have accurately predicted the trajectory of this technology.
Reply
[-]enterthewoods16d131
(Apologies for the long comment). 
I want to make a point about your arguments about the growth of time horizons being superexponential. I think they are generally correct, but I think they need to be downweighted somewhat in the timeline model. 
This is how I understand your model:
> Our starting point is to take the METR graph and extrapolate it exponentially, as they do, making a guess about what agentic coding time horizon would correspond to the AC milestone.
And then you include adjustments to this extrapolation, some of which are arguments about superexponential growth that don't have anything to do with AI R&D speedups feeding back into themselves. Because you are using a threshold on the METR graph to determine when ACs happen, these arguments about superexponential growth meaningfully affects your prediction of time to ACs.
I consider the casual network to look something like this:(The METR time horizon level and the level of AI R&D speedup are both driven by the level of effective compute.)
Since we only  _truly_ care about AI R&D speedup, we must differentiate between arguments about how fast effective compute will advance or how these advances affect the R&D speedup (which both affect AI R&D speedup and the time to ACs), and arguments about how much effective compute will affect the METR time horizon (which is not what we ultimately care about).
The argument that superexponential growth is implied by infinite time horizons is purely an argument about the relationship between effective compute and the METR time horizon. Whether or not it is true does not change the level of effective compute you need to get ACs. This also applies to your second argument for superexponential growth (that doublings get easier to achieve naturally because less effective compute is needed to jump from 1 month to 4 months than from 1 week to 4 weeks, for example). Again this is only an argument about how increases in effective compute affect the METR time horizon graph, not how fast effective compute is increasing or how increases in effective compute increase AI R&D speedup. 
Now this doesn’t mean you have to throw out this entire section of the model. Importantly, it seems like there should be at least some correlation between the **relationship between effective compute and the METR time horizon** and the **relationship between effective compute and the AI R &D speedup**.But unless this correlation is 1-1, arguments about superexponential growth that come from the relationship between effective compute and the METR time horizon should be downweighted.
Here’s a toy model to illustrate this better:
Imagine there are four effective compute levels: X1, X2, X3, and X4. X1 is where we are at right now. Let’s say that if METR is exponential in relation to effective compute, we hit Y horizon length at effective compute level X4. On the other hand, if METR is superexponential in relation to effective compute, we hit Y horizon length at capability level X2. Let’s imagine that we thought we would get ACs at effective compute level X4, around where METR was supposed to hit horizon length Y if it were exponential. Suppose we now know that the METR graph is superexponential and will hit Y at X2. How should that affect our expectation of when we will hit ACs? If the correlation between the relationship of effective compute to the METR time horizon and the relationship of effective compute to AI R&D speedup is 1-1, we should update to X2. If there is no correlation, we should keep our estimation at X4. If there is some correlation, maybe we say X3?
The consequences of this are, I think, slightly longer timelines from the model. 
Reply
[-]elifland15d52
Thanks for the thoughts! I'm not sure I exactly understand your point. I do think that we should think about the relationship between the time horizon and the AC effective compute requirement directly, which is why we chose to use this to set the effective compute requirement. If a model has achieved very high time horizons, then we think that this is direct evidence for them being an AC. Note that we also optionally have an effective compute gap as well, to be added after reaching the time horizon requirement.
I'm also not sure what you mean about the relationship being 1-1, like why we should increase the effective compute requirement rather than decrease if we instead had decided to try to use AI R&D speedup to anchor the requirement. Why would we think that setting the effective compute requirement via the AI R&D speedup would predictably give a higher effective compute requirement? I don't think the METR trend being superexponential implies anything one way or the other. They are just different metrics and thus we would use a totally different method if we had instead tried to set the requirement using AI R&D speedup. I'm not immediately sure what method would be best though given that we don't have a trend for it. If we had more high-quality data on coding uplift over time, then that could help us out, and I think that would be a reasonable alternative thing to extrapolate (Daniel discusses this a bit in the post), but I don't have a prior on whether it would lead to a lower or higher requirement than extrapolating time horizons (in fact a quick guess would be that it would lead to a lower requirement, given Opus 4.5's reported much higher uplift than Sonnet 4.5).
Reply
[-]enterthewoods15d51
Thanks for the reply!
> I'm not sure I exactly understand your point.
It is a very confusing point and I didn't explain it well, sorry. I also might just be fundamentally confused and wrong. Hopefully this comment can explain it well enough so you can either shoot it down as incorrect or accept it. 
First of all, it might be easier to understand if we replace "effective compute" with "general capabilities" in my original comment. Effective compute causally affects capabilities which causally affects both the METR time horizon measurement and the AI R&D speedup variable. So we can screen off the effective compute node and replace it with a general capabilities node. 
> If a model has achieved very high time horizons, then we think that this is direct evidence for them being an AC.
I mostly agree with this. However, I think a model having very high time horizons is direct evidence for capabilities being high, which is then direct evidence for AI R&D speedup being high (and thus more likely to be past the AC threshold). 
This leads to an important distinction. If you want to argue that AI R&D speedup will be higher (or certain thresholds of AI R&D speedup like AC being reached faster) using the METR graph, your argument fundamentally _has to be an argument about capabilities being higher_ (_arriving sooner) or an argument about the relationship between capabilities and AI R &D speedup. _
I don't think that your first argument about the superexponential nature of the METR graph (that infinite time horizons in finite time implies it must be superexponential) is either of these. It seems to be an argument purely about the relationship between capabilities and the METR graph. 
I also think your second argument for superexponential growth (that subsequent doublings should get easier because they require less new capabilities than earlier doublings) is mostly an argument about the relationship between capabilities and the METR graph. Although I could maybe see it being an argument about the relationship between capabilities and AI R&D speedup (the last few capabilities to be unlocked provide huge boosts to AI R&D speedup?). 
Basically, my core concern is if neither of these arguments are truly arguments about capabilities arriving faster, or about the relationship between capabilities and AI R&D speedup, then why are they being used to update the estimation of time to ACs?
In contrast, arguments about AI R&D feeding back into itself, or about compute investment slowing down, are arguments directly about how fast capabilities will advance. So these validly affect your estimation of time to ACs. 
If you disagree with me and think that the arguments for superexponential growth are not just arguments about the relationship between capabilities and the METR graph, then we can focus our discussion there. To the extent that these are also arguments about the things we truly care about, you should adjust time to ACs based on them; this is what I was trying to capture with the correlation stuff in my first comment.
If you do end up agreeing, I also don't really know how to incorporate this into the model. It seems hard and messy. I think that it is true that using the METR graph is the best thing we can do right now. I just don't think that these arguments about the superexponential nature of the METR graph should affect the estimation of time to ACs. But I _do_ think that it is superexponential...and I think that it is the best way to estimate time to ACs...so again, messy :(. Hope this makes more sense!
Reply
[-]Thomas Larsen17d*Ω482
>   * Relatedly, I’m also interested in the simple method of extrapolating AI revenue growth trends until AI revenue is most of the world economy. That seems like a decent proxy for when AGI will be achieved. I trust this method less than our model for obvious reasons, but I still put some weight on it. What does it say? Well, it says “Early 2030s.” OK.
> 

I'm curious why you trust revenue extrapolation less than the model. Intuitively revenue seems like a better thing to extrapolate to me than benchmarks or flops or whatever because it's much less gameable and there's a much more clear threshold for AGI (revenue is similar size to GDP). 
Reply
1
[-]elifland17dΩ594
I think revenue extrapolations seem like a useful exercise. But I think they provide much less evidence than our model.
Which revenues would you extrapolate? You get different results for e.g. doing OpenAI vs. Nvidia.
Also (most importantly) are you saying we should assume that log(revenue) is a straight line? 
  * If so, that seems like a really bad assumption given that usually startup revenue growth rates slow down a lot as revenue increases, so that should be the baseline assumption.
  * If not, how else do we predict how the revenue trend will change without thinking about AI capabilities? We could look at base rates for startups that have this level of revenue growth early on, but then obviously none of those revenue trends have ever grown until world GDP, so that would say AGI never.

edited to add: relevant graph from <https://epoch.ai/gradient-updates/openai-is-projecting-unprecedented-revenue-growth>:

> much more clear threshold for AGI
Also I disagree with this, I think time horizon is about as good as revenue on this dimension, maybe a bit better. Both are hugely uncertain though of course.
Reply
1
[-]Josh You14d*20
Most successful startups slow down a lot after a brief hypergrowth phase. We should be looking for signs that AI companies like OpenAI and Anthropic* are experiencing unusually long and persistent hypergrowth: surprisingly little slowdown in growth, or maintaining >2x growth/year at surprisingly high revenue levels like 100B. They are both already growing very surprisingly fast for companies with multiple billions in revenue, to be clear, but whether that continues is valuable evidence.
This could be a sign that present-day models have a higher economic ceiling than we realize (closer to TAI than they might look), or that companies are making real progress towards transformative AI. Most companies don't dramatically improve their product lineup over and over again after they find initial product-market-fit, so sustained rapid growth means that AI development is leading to a new batch of successful products on a regular basis, i.e. escalating economic usefulness.
*I think companies that serve AI to end-users are the most useful indicators
Reply
[-]Daniel Kokotajlo17dΩ220
I basically agree with Eli, though I'll say that I don't think the gap between extrapolating METR specifically and AI revenue is huge. I think ideally I'd do some sort of weighted mix of both, which is sorta what I'm doing in my ATC.
Reply
[-]AnthonyC17d42
Thanks! Letting us play with the assumptions is a great way to develop an intuitive sensitivity analysis.
Reply
1
Moderation Log
More from elifland
674AI 2027: What Superintelligence Looks Like
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/ai-2027.com/>)Ω
Daniel Kokotajlo, Thomas Larsen, elifland, Scott Alexander, Jonas V, romeo
9mo
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/ai-2027.com/>)Ω
222
38Response to titotal’s critique of our AI 2027 timelines model
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/aifuturesnotes.substack.com/p/response-to-titotals-critique-of>)
elifland, Daniel Kokotajlo
1mo
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/aifuturesnotes.substack.com/p/response-to-titotals-critique-of>)
6
91Slow corporations as an intuition pump for AI R&D automation
Ω
ryan_greenblatt, elifland
8mo
Ω
25
View more
Curated and popular this week
2292025 in AI predictions
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)
jessicata
2d
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)
19
304In My Misanthropy Era
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/jenn.site/in-my-misanthropy-era/>)
jenn
5d
[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/</recommendations>)[](https://lesswrong.com/posts/YABG5JmztGGPwNFq2/<https:/jenn.site/in-my-misanthropy-era/>)
138
134Backyard cat fight shows Schelling points preexist language
jchan
3d
20
30Comments
30
x
AI Futures Timelines and Takeoff Model: Dec 2025 Update — LessWrong
PreviousNext
