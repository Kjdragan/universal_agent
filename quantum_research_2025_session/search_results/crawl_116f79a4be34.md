---
title: "Researchers Achieve Quantum Computing Milestone, Realizing Certified Randomness | College of Natural Sciences"
source: https://cns.utexas.edu/news/research/researchers-achieve-quantum-computing-milestone-realizing-certified-randomness
date: 2025-03-26
description: "A team including Scott Aaronson demonstrated what may be the first practical application of quantum computers to a real world problem"
word_count: 1158
---

 Research 
# Researchers Achieve Quantum Computing Milestone, Realizing Certified Randomness
March 26, 2025 • by Staff Writer 
A team including Scott Aaronson demonstrated what may be the first practical application of quantum computers to a real world problem.
!An arc of green orbs floats above a golden surface
Using a 56-qubit quantum computer, researchers have for the first time experimentally demonstrated a way of generating random numbers from a quantum computer and then using a classical supercomputer to prove they are truly random and freshly generated. Image credit: Quantinuum.
In a new paper in  _Nature_, a team of researchers from JPMorganChase, Quantinuum, Argonne National Laboratory, Oak Ridge National Laboratory and The University of Texas at Austin describe a milestone in the field of quantum computing, with potential applications in cryptography, fairness and privacy. 
Using a 56-qubit quantum computer, they have for the first time experimentally demonstrated certified randomness, a way of generating random numbers from a quantum computer and then using a classical supercomputer to prove they are truly random and freshly generated. This could pave the way towards the use of quantum computers for a practical task unattainable through classical methods.
Scott Aaronson, Schlumberger Centennial Chair of Computer Science and director of the Quantum Information Center at UT Austin, invented the certified randomness protocol  that was demonstrated. He and his former postdoctoral researcher, Shih-Han Hung, provided theoretical and analytical support to the experimentalists on this latest project.
“When I first proposed my certified randomness protocol in 2018, I had no idea how long I’d need to wait to see an experimental demonstration of it,” Aaronson said. “Building upon the original protocol and realizing it is a first step toward using quantum computers to generate certified random bits for actual cryptographic applications.”
Quantum computers have been shown to possess computational power far beyond that offered by even the most powerful classical supercomputers. Last year, a team from Quantinuum and JPMorganChase and another from Google each announced they had performed tasks on their respective quantum computers that would have been impossible with existing supercomputers, a feat known as quantum supremacy. However, converting this power into solving a practical task remained an open challenge.
This challenge has now been addressed by leveraging random circuit sampling (RCS) to generate certified randomness. Randomness is an essential resource for many applications in areas such as cryptography, fairness and privacy.
Classical computers alone cannot generate truly random numbers, so they are typically combined with a hardware random-number generator. But an adversary could commandeer the random-number generator and use it to provide the computer with numbers that are not fully random, allowing the adversary to then crack cryptographic codes. Using the new method described here, even if an adversary had commandeered the quantum computer, it would theoretically be impossible for them to manipulate the output and still be certified as random.
Accessing the 56-qubit Quantinuum System Model H2 trapped-ion quantum computer remotely over the internet, the team generated certifiably random bits. Specifically, they performed a certified-randomness-expansion protocol based on RCS, which outputs more randomness than it takes as input. 
The protocol consists of two steps. In the first step, the team repeatedly fed the quantum computer challenges that it had to quickly solve which even the world’s most powerful classical supercomputer can’t quickly solve and which the quantum computer can only solve by picking one of the many possible solutions at random.
In the second step, the randomness was mathematically certified to be genuine using classical supercomputers. In fact, the team showed that randomness could not be mimicked by classical methods. Using classical certification across multiple leadership-scale supercomputers with a combined sustained performance of 1.1 x 1018 floating point operations per second (1.1 ExaFLOPS), the team certified 71,313 bits of entropy. 
“This work marks a major milestone in quantum computing, demonstrating a solution to a real-world challenge using a quantum computer beyond the capabilities of classical supercomputers today,” said Marco Pistoia, Head of Global Technology Applied Research and Distinguished Engineer, JPMorganChase. “This development of certified randomness not only shows advancements in quantum hardware, but will be vital to further research, statistical sampling, numerical simulations and cryptography.”
In June 2024, Quantinuum upgraded its System Model H2 quantum computer to 56 trapped-ion qubits and, in partnership with JPMorganChase’s Global Technology Applied Research team, used this system to perform RCS, a task that was originally designed to demonstrate quantum advantage. H2 improved on the existing industry state of the art by a factor of 100 thanks to its high fidelity and all-to-all qubit connectivity, leading to the conclusion that the result could not have been obtained on any existing classical computers. This upgrade, combined with Aaronson’s protocol, led to the breakthrough now described in Nature.
“Today, we celebrate a pivotal milestone that brings quantum computing firmly into the realm of practical, real-world applications,” said Dr. Rajeeb Hazra, President and CEO of Quantinuum. “Our application of certified quantum randomness not only demonstrates the unmatched performance of our trapped-ion technology but sets a new standard for delivering robust quantum security and enabling advanced simulations across industries like finance, manufacturing and beyond. At Quantinuum, we are driving pioneering breakthroughs to redefine industries and unlock the full potential of quantum computing.”
“These results in quantum computing were enabled by the world-leading U.S. Department of Energy computing facilities at Oak Ridge National Laboratory, Argonne National Laboratory and Lawrence Berkeley National Laboratory,” said Travis Humble, director of the Quantum Computing User Program and director of the Quantum Science Center, both at ORNL. “Such pioneering efforts push the frontiers of computing and provide valuable insights into the intersection of quantum computing and high-performance computing.”
For a more detailed description, read Scott Aaronson’s blog post.
_This post is adapted from a press release by JPMorganChase and Quantinuum._
## Share
  * Share on Facebook
  * Share on X
  * Share on LinkedIn
  * Show and hide the URL copier tool.
  * Share via email

Copy this page's URL
### Tags
  * Computer Science

!A woman with long hair smiles, standing before a slatted wooden structure indoors.
Accolades
### Computer Scientist Kristen Grauman Wins Hill Prize in Artificial Intelligence 
January 16, 2026 • by Staff Writer 
!A fist is superimposed on the ghostly outline of an open palm.
Department of Computer Science 
### Adaptive Anatomy: 3D Models That Fit Every Form 
December 19, 2025 • by Karen Davidson 
!A man holds a microphone and speaks to a group, in front of a banner that reads "Good Systems: A UT Grand Challenge Designing AI technologies that benefit society is our grand challenge" and a slide titled "AI systems that understand what humans want" as a cartoon girl's thought bubble reads "hidden state" and arrows pointing to the words dataset and estimate of hidden state are labeled "human input by psychological process" and "inverse algorithm derived from model of psychological process"
UT Bridging Barriers 
### Cross-Cutting Edge: Good Systems Scholar Refines Alignment Research  
November 24, 2025 • by Michael Wolman 
