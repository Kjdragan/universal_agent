---
title: "IBM roadmap to quantum-centric supercomputers (Updated 2024) | IBM Quantum Computing Blog"
source: https://www.ibm.com/quantum/blog/ibm-quantum-roadmap-2025
date: 2022-05-10
description: "The updated IBM Quantum roadmap: weaving quantum processors, CPUs, and GPUs into a compute fabric to solve problems beyond the scope of classical resources."
word_count: 2172
---

  * Quantum Research
  * Blog

# Expanding the IBM Quantum roadmap to anticipate the future of quantum-centric supercomputing
We are explorers. We’re working to explore the limits of computing, chart the course of a technology that has never been realized, and map how we think these technologies will benefit our clients and solve the world’s biggest challenges. But we can’t simply set out into the unknown. A good explorer needs a map.

Date
10 May 2022
Authors
Jay Gambetta
Topics
Share this blog
A challenge of near-term quantum computation is the limited number of available qubits. Suppose we want to run a circuit for 400 qubits, but we only have 100 qubit devices available. What do we do? Read about circuit knitting with classical communication.
**Disclaimer: The below blog represents our latest developments from 2022. IBM has since updated the development roadmap as we learn more about the engineering and innovations required to realize error-corrected quantum computing. Please refer tothis page for the latest roadmap and our latest progress along it.**
Two years ago, we issued our first draft of that map to take our first steps: our ambitious three-year plan to develop quantum computing technology, called our development roadmap. Since then, our exploration has revealed new discoveries, gaining us insights that have allowed us to refine that map and travel even further than we’d planned. Today, we’re excited to present to you an update to that map: our plan to weave quantum processors, CPUs, and GPUs into a compute fabric capable of solving problems beyond the scope of classical resources alone.
Our goal is to build quantum-centric supercomputers. The quantum-centric supercomputer will incorporate quantum processors, classical processors, quantum communication networks, and classical networks, all working together to completely transform how we compute. In order to do so, we need to solve the challenge of scaling quantum processors, develop a runtime environment for providing quantum calculations with increased speed and quality, and introduce a serverless programming model to allow quantum and classical processors to work together frictionlessly.
But first: where did this journey begin? We put the first quantum computer on the cloud in 2016, and in 2017, we introduced an open source software development kit for programming these quantum computers, called Qiskit. We debuted the first integrated quantum computer system, called the IBM Quantum System One, in 2019, then in 2020 we released our development roadmap showing how we planned to mature quantum computers into a commercial technology. 
As part of that roadmap, in 2021 we released our 127-qubit IBM Quantum Eagle
IBM Quantum broke the 100‑qubit processor barrier in 2021. Read more about Eagle.
processor and launched Qiskit Runtime, a runtime environment of co-located classical systems and quantum systems built to support containerized execution of quantum circuits at speed and scale. The first version gave a 120x speedup
In 2021, we demonstrated a 120x speedup in simulating molecules thanks to a host of improvements, including the ability to run quantum programs entirely on the cloud with Qiskit Runtime.
on a research-grade quantum workload. Earlier this year, we launched the Qiskit Runtime Services with primitives: pre-built programs that allow algorithm developers easy access to the outputs of quantum computations without requiring intricate understanding of the hardware. 
Now, our updated map will show us the way forward. **Learn more in our latest roadmap videohere.**
!Updating the IBM Quantum Roadmap to anticipate the future of quantum-centric supercomputing
IBM Quantum Roadmap, May 2022
!IBM-Quantum_Development-Roadmap_2024.jpg
Updated IBM Quantum Roadmap, August 2024
## Preparing for serverless quantum computation
In order to benefit from our world-leading hardware, we need to develop the software and infrastructure so that our users can take advantage of it. Different users have different needs and experiences, and we need to build tools for each persona: kernel developers, algorithm developers, and model developers. 
For our kernel developers — those who focus on making faster and better quantum circuits on real hardware — we’ll be delivering and maturing Qiskit Runtime. First, we will add dynamic circuits, which allow for feedback and feedforward of quantum measurements to change or steer the course of future operations. Dynamic circuits extend what the hardware can do by reducing circuit depth, by allowing for alternative models of constructing circuits, and by enabling parity checks of the fundamental operations at the heart of quantum error correction.
To continue to increase the speed of quantum programs in 2023, we plan to bring threads to the Qiskit Runtime, allowing us to operate parallelized quantum processors, including automatically distributing work that is trivially parallelizable. In 2024 and 2025, we’ll introduce error mitigation and suppression techniques into Qiskit Runtime so that users can focus on improving the quality of the results obtained from quantum hardware. These techniques will help lay the groundwork for quantum error correction in the future.
However, we have work to do if we want quantum will find broader use, such as among our algorithm developers — those who use quantum circuits within classical routines in order to make applications that demonstrate quantum advantage. 
For our algorithm developers, we’ll be maturing the Qiskit Runtime Service’s primitives. The unique power of quantum computers is their ability to generate non-classical probability distributions at their outputs. Consequently, much of quantum algorithm development is related to sampling from, or estimating properties of these distributions. The primitives are a collection of core functions to easily and efficiently work with these distributions. 
Typically, algorithm developers require breaking problems into a series of smaller quantum and classical programs, with an orchestration layer to stitch the data streams together into an overall workflow. We call the infrastructure responsible for this stitching Quantum Serverless
To bring value to our users, we need our programing model to fit seamlessly into their workflows, where they can focus on their code and not have to worry about the deployment and infrastructure. We need a serverless architecture.
. Quantum Serverless centers around enabling flexible quantum-classical resource combinations without requiring developers to be hardware and infrastructure experts, while allocating just those computing resources a developer needs when they need them. In 2023, we plan to integrate Quantum Serverless into our core software stack in order to enable core functionality such as circuit knitting. 
What is circuit knitting? Circuit knitting techniques break larger circuits into smaller pieces to run on a quantum computer, and then knit the results back together using a classical computer. 
Earlier this year, we demonstrated1 a circuit knitting method called entanglement forging to double the size of the quantum systems we could address with the same number of qubits. However, circuit knitting requires that we can run lots of circuits split across quantum resources and orchestrated with classical resources. We think that parallelized quantum processors with classical communication will be able to bring about quantum advantage even sooner, and a recent paper suggests a path forward.
With all of these pieces in place, we’ll soon have quantum computing ready for our model developers — those who develop quantum applications to find solutions to complex problems in their specific domains. We think by next year, we’ll begin prototyping quantum software applications for specific use cases. We’ll begin to define these services with our first test case — machine learning — working with partners to accelerate the path toward useful quantum software applications. By 2025, we think model developers will be able to explore quantum applications in machine learning, optimization, natural sciences, and beyond.
## Solving the scaling problem
Of course, we know that central to quantum computing is the hardware that makes running quantum programs possible. We also know that a quantum computer capable of reaching its full potential could require hundreds of thousands, maybe millions of high-quality qubits, so we must figure out how to scale these processors up. With the 433-qubit “Osprey” processor and the 1,121-qubit “Condor” processors — slated for release in 2022 and 2023, respectively — we will test the limits of single-chip processors and controlling large-scale quantum systems integrated into the IBM Quantum System Two. But we don’t plan to realize large-scale quantum computers on a giant chip. Instead, we’re developing ways to link processors together into a modular system capable of scaling without physics limitations.
To tackle scale, we are going to introduce three distinct approaches. First, in 2023, we are introducing “Heron”: a 133-qubit processor with control hardware that allows for real-time classical communication between separate processors, enabling the knitting techniques described above. The second approach is to extend the size of quantum processors by enabling multi-chip processors. “Crossbill,” a 408 qubit processor, will be made from three chips connected by chip-to-chip couplers that allow for a continuous realization of the heavy-hex lattices across multiple chips. The goal of this architecture is to make users feel as if they’re just using just one, larger processor.
!The 133-qubit “Heron” processor, slated for 2023.
The 133-qubit “Heron” processor, slated for 2023.
Along with scaling through modular connection of multi-chip processors, in 2024, we also plan to introduce our third approach: quantum communication between processors to support quantum parallelization. We will introduce the 462-qubit “Flamingo” processor with a built-in quantum communication link, and then release a demonstration of this architecture by linking together at least three Flamingo processors into a 1,386-qubit system. We expect that this link will result in slower and lower-fidelity gates across processors. Our software needs to be aware of this architecture consideration in order for our users to best take advantage of this system.
!Quantum communication via two-qubit gates between separate chips.
Quantum communication via two-qubit gates between separate chips.
Our learning about scale will bring all of these advances together in order to realize their full potential. So, in 2025, we’ll introduce the “Kookaburra” processor. Kookaburra will be a 1,386 qubit multi-chip processor with a quantum communication link. As a demonstration, we will connect three Kookaburra chips into a 4,158-qubit system connected by quantum communication for our users.
!In 2025, we’ll introduce the 1,386-qubit multi-chip processor, “Kookaburra.” With its communication link support for quantum parallelization, three Kookaburra chips can connect into a 4,158-qubit system.
In 2025, we’ll introduce the 1,386-qubit multi-chip processor, “Kookaburra.” With its communication link support for quantum parallelization, three Kookaburra chips can connect into a 4,158-qubit system.
The combination of these technologies — classical parallelization, multi-chip quantum processors, and quantum parallelization — gives us all the ingredients we need to scale our computers to wherever our roadmap takes. By 2025, we will have effectively removed the main boundaries in the way of scaling quantum processors up with modular quantum hardware and the accompanying control electronics and cryogenic infrastructure. Pushing modularity in both our software and our hardware will be key to achieving scales well ahead of our competitors, and we’re excited to deliver it to you.
## The quantum-centric supercomputer
Our updated roadmap takes us as far as 2025 — but development won’t stop there. By then, we will have removed some of the biggest roadblocks in the way of scaling quantum hardware, while developing the tools and techniques capable of integrating quantum into computing workflows. This sea change will be the equivalent of replacing paper maps with GPS satellites as we navigate into the quantum future.
> This sea change will be the equivalent of replacing paper maps with GPS satellites.
We aren’t just thinking about quantum computers, though. We’re trying to induce a paradigm shift in computing overall. For many years, CPU-centric supercomputers were society’s processing workhorse, with IBM serving as a key developer of these systems. In the last few years, we’ve seen the emergence of AI-centric supercomputers, where CPUs and GPUs work together in giant systems to tackle AI-heavy workloads.
Now, IBM is ushering in the age of the quantum-centric supercomputer, where quantum resources — QPUs — will be woven together with CPUs and GPUs into a compute fabric. We think that the quantum-centric supercomputer will serve as an essential technology for those solving the toughest problems, those doing the most ground-breaking research, and those developing the most cutting-edge technology.
We may be on track, but exploring uncharted territory isn’t easy. We’re attempting to rewrite the rules of computing in just a few years. Following our roadmap will require us to solve some incredibly tough engineering and physics problems.
But we’re feeling pretty confident — we’ve gotten this far, after all, with the new help of our world-leading team of researchers, the IBM Quantum Network, the Qiskit open source community, and our growing community of kernel, algorithm, and model developers. We’re glad to have you all along for the ride as we continue onward.
## Learn more about:
Quantum Chemistry: Few fields will get value from quantum computing as quickly as chemistry. Even today’s supercomputers struggle to model a single molecule in its full complexity. We study algorithms designed to do what those machines can’t.
References
  1. Eddins, A., Motta, M., Gujarati, T., et al. Doubling the size of quantum simulators by entanglement forging. arXiv. (2021)
| ↩

### IBM Quantum: Tomorrow's computing today
Quantum starts here(opens in a new tab)
### Keep exploring
View all blogs

