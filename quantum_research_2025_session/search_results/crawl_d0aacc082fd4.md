---
title: "Scaling for quantum advantage and beyond | IBM Quantum Computing Blog"
source: https://www.ibm.com/quantum/blog/qdc-2025
date: 2025-11-12
description: "IBM® lays out the groundbreaking advances in algorithms, hardware, and software that will empower our community to achieve quantum advantage together."
word_count: 1960
---

  * Quantum Research
  * Blog

# Scaling for quantum advantage and beyond
At Quantum Developer Conference 2025, IBM® lays out the groundbreaking advances in algorithms, hardware, and software that will empower our community to achieve quantum advantage together.
Date
12 Nov 2025
Authors
Ryan Mandelbaum
Topics
AlgorithmsCommunityError Correction & MitigationNetworkSoftwareSystemsQiskit
Share this blog
Quantum advantage isn’t the finish line of the quantum computing marathon. In fact, it’s closer to the start. Quantum advantage means that quantum + classical methods can provably outperform purely classical methods. As these advantages emerge, we must be ready to scale them—and our systems—with the goal of realizing useful quantum computing.
At the kickoff of this week’s Quantum Developer Conference (QDC), we delivered our annual State of the Union address, presenting new tools and research to help our community achieve and scale quantum advantage. As promised, we checked new processors and software advances off of our roadmap. But we also presented a vision for how we're building the future of computing itself.
So, what did we announce and how do you get started? Read on for more.
!2025-QDC_Development & Innovation Roadmap.jpg
Updated 2025 IBM Quantum Roadmap
## Three candidates for quantum advantage
Last year, Jay Gambetta, Director of IBM Research, said that we’d see quantum advantage by the end of 2026, provided that the quantum and HPC communities work together. Now, the quantum community is starting to make its first credible advantage claims.
Earlier this year, we published a framework for rigorously gauging how and when we’ll have entered the advantage era. Today, we’re at an exciting juncture in the history of this technology. We're already seeing examples of enterprises building potentially useful quantum-powered alternatives to production classical solutions. At the same time, theorists are seeking rigorous proofs of advantage by verifying quantum circuit implementations against trustworthy classical methods.
Our team has been working to find circuits with separations over classical computing that can be rigorously validated. **At QDC, we presented candidate advantage experiments across three categories** : observable estimation, variational algorithms, and problems with efficient classical verification. Still, we argue that the community hasn’t achieved advantage yet, because we have not yet met key criteria established in the advantage framework: namely, rigorous _validation_ of the quantum computation and a demonstrable _quantum separation_ measured in terms of efficiency, cost-effectiveness, accuracy, or some combination of the three.
That's why IBM, Flatiron, BlueQubit, and Algorithmiq have contributed to **an open, community-led advantage tracker**. The tracker allows users to systematically monitor and evaluate promising candidates of quantum advantage—and how these candidates stack up against the leading classical methods.
Learn more about the latest quantum advantage candidates with the new Quantum Advantage Tracker here.
!qat.png
New Quantum Advantage Tracker invites users to monitor and evaluate demonstrations of quantum advantage.
## New capabilities for scaling advantage
Meanwhile, it’s our job at IBM to build the tools that enable the quantum community to find and extend advantage, from users validating advantages with quantum circuits to those seeking to accelerate applications with quantum.
!IBM-Quantum_Nighthawk_Held_6.jpg
IBM Quantum Nighthawk chip (Credit: IBM)
First, quantum advantage requires high-performing hardware. This year, we unveiled the 120-qubit **IBM Quantum Nighthawk**. Nighthawk is our first chip with a square qubit topology, increasing the number of couplers from Heron’s 176 to 218. This lets developers design circuits 30% more complex with fewer SWAP gates, allowing them to tackle bigger problems.
Nighthawk is designed to scale both modularly and in performance. We plan to improve the Nighthawk line of chips with revisions that can run circuits at 5,000, 7,500, 10,000, and ultimately 15,000 quantum gates. We promised a Nighthawk capable of running 5,000 gates by the end of 2025, and we project that we’ll be able to hit that milestone.
Take a closer look at the new IBM Quantum Nighthawk here.
!IBM-Quantum_Nighthawk_6.jpg
IBM Quantum Nighthawk's qubit plane includes 120 qubits arranged in a square lattice.
We also released the highest-performing **IBM Quantum Heron** yet. Now in its third revision, Heron features the lowest median two-qubit gate errors to date—of its 176 possible two-qubit couplings, 57 of them deliver less than one error in every 1000 operations. On top of that, we've achieved a new record on our fleet of Herons: **330,000 CLOPS** , compared to 200,000 at the end of 2024. That lets us run the quantum utility experiment in less than 60 minutes, well over 100x faster than we could in 2023. And as a commitment to our community, our QDC attendees will be given exclusive access to the `ibm_boston` Heron r3 chip during the conference.
View QPU details on IBM Quantum Platform.
Equally important for advantage workloads is a high-performing software development kit—and the open-source Qiskit SDK continues to be the preferred and highest-performing open-source quantum SDK. Latest benchmarks find that Qiskit SDK v2.2 is 83x faster in transpiling than Tket 2.6.0.
Learn more about Qiskit SDK v2.2 on the IBM Quantum blog or read the full release notes.
!Qiskit Diagram \(1\).jpg
2025 Qiskit software stack
Meanwhile, finding and validating advantage requires a high level of control as developers build and optimize their circuits. Qiskit v2.1 enabled box annotations, where users can add flags to specific regions of a circuit. Now, the **Samplomatic** package lets you add customizations to those regions. Then, it transforms those customizations into a template plus a new object called the **samplex** , which provides semantics for circuit randomization. Users pass the circuit template and the samplex to the new **executor primitive** , altogether offering a far more efficient way to apply advanced and composable error mitigation techniques.
Get started with the Samplomatic GitHub package here.
Annotations also enable circuit improvements—for example, building and running **scalable dynamic circuits.** Dynamic circuits incorporate classical operations in the middle of a circuit run, leveraging mid-circuit measurement and feeding information forward to make conditional changes to the rest of the circuit. Circuit annotations allow users to perform deferred timing and stretch operations, and in a demo shown at QDC, we used the stretch functionality to add dynamical decoupling to qubits that were idle during concurrent measurements and feedforward operations.
The result? We saw up to **25% more accurate results with a 58% reduction in ​two-qubit gates** at the 100+ qubit scale. That includes using dynamic circuits over static circuits for a demo involving a 46-site Ising model simulation with 8 Trotter steps.​ In other words, we showed that it is now possible to run **dynamic circuits at the utility scale** —and that they provide tangible benefits over static circuits.
Read our documentation for more on utility-scale dynamic circuits.
Further, samplomatic allows users more control and flexibility when running **advanced classical error mitigation tools.** Probabilistic error cancellation (PEC) is an error mitigation method that removes the bias from a noisy quantum circuit and provides noise-free expectation values. However, it comes with substantial sampling overhead. The improved control offered by samplomatic lets you add advanced classical error mitigation methods to circuits to decrease the sampling overhead of PEC by 100x.
Add advanced classical error mitigation tools to your circuits with the new propagated noise absorption and shaded lightcones addons.
We’ve always said that quantum advantage will only come if quantum and classical work together. This year, we've seen numerous examples of how the global quantum community is expanding into HPC. However, scientific programmers work mainly in compiled languages like C++, and Qiskit was originally built in Python, an interpreted language.
That's why, with Qiskit v2.x, we introduced a **C API** leveraging a foreign function interface to enable bindings to any other programming language, either compiled or interpreted. Through the C API, Qiskit achieves deeper integration with HPC systems, allowing quantum-classical workloads to run efficiently wherever they are deployed. We recently built a **C++ interface** on top of the C API, highlighted at this year's QDC and on the IBM Quantum blog with a new quantum + HPC workflow demo.
Explore Qiskit C++ and the new quantum + HPC workflow demo on GitHub, or read our C API documentation to learn more.
Together, the circuits and algorithms for advantage will ultimately power quantum application libraries—and we’ve committed to realize application libraries by 2027. At this year’s QDC, we showed state-of-the-art algorithms progress in the four key areas we expect to make up those libraries: Hamiltonian simulation, optimization, machine learning, and differential equations.
En route to that goal, we debuted Qiskit Functions a year early last year—and already, our partners at E.ON, Yonsei, and ColibriTD have published research with the help of functions from Q-CTRL and Qunova Computing. This year, Qunova Computing, Kipu Quantum, Colibri TD, and Global Data Quantum have contributed **new Qiskit Functions** to the catalog. We encourage you to check out the functions on IBM Quantum Platform.
Explore the Qiskit Functions Catalog.
## Building our fault-tolerant architecture
While we enable our community to achieve and scale quantum advantage, IBM Quantum is focused on pushing along our roadmap to scale for fault-tolerant quantum computing. Success requires that we iterate and learn as quickly as possible, carrying those lessons forward as we release new hardware.
Driving our cycles of learning are new fabrication processes that let us create more chips, faster. This year, we revealed the process behind our chips—all of our chips begin on **300mm wafers** at the state-of-the-art, always-on Albany NanoTech Complex, incorporating the latest advances in 300mm technology with our world-leading semiconductor and quantum expertise at IBM Research. These processes let us double the speed of R+D for our latest chips by slicing the wafer processing time in half—all while producing a chip ten times more complex than anything we have released prior.
Learn more about why we’re using 300mm technology to fabricate quantum chips here.
!IBM-Quantum_Loon_300mm_1.jpg
IBM researcher holding a 300mm IBM Quantum Loon wafer (Credit: IBM)
Those lessons set the stage for **IBM Quantum Loon** —a proof-of-concept processor that demonstrates many of the key components needed to implement our quantum low-density parity check (qLDPC) codes.
We've previously demonstrated our ability to achieve 6-way qubit connections, increase layers of routing on the chip surface, create physically longer couplers, and build reset gadgets that quickly reset the qubit to the ground state. With Loon, for the first time, we test all these features together, aided by new electronic design automation (EDA) to realize more complex architectures than ever before.
Today, Loon is almost out of fabrication, and will be assembled by the end of the year.
!IBM-Quantum_Loon_Render_1.jpg
IBM Quantum Loon (Credit: IBM)
!IBM-Quantum_Loon_Render_3.jpg
IBM Quantum Loon includes c-couplers to enable long-range connections between distant qubits on the chip (Credit: IBM)
Finally, we once again checked something off of our roadmap a year early. Error correction requires **an error correction decoder** that can decode errors in real time. Earlier this year we announced **RelayBP** , a flexible, accurate, fast and compact decoding algorithm. More recently, we announced that we’d implemented RelayBP on an AMD FPGA. Now, we can complete a decoding task in less than 480ns—approximately an order of magnitude faster than the startup cost of other leading industry solutions. There’s work yet to scale the decoder, but we’re proud to have overcome this critical hurdle.
Read the RelayBP blog here and read the newest paper here.
## Quantum advantage together
This year brought more than new capabilities. Quantum advantages are emerging, and improving hardware will lead to definitive speed-ups over classical computing. New software tools will take those advantages and allow users to implement them in algorithms, and run those algorithms across integrated quantum and classical resources. As we continue to deliver on our roadmap, we’re confident that the community will bring quantum-centric supercomputing into reality. Now all we need is you, our users, to join us on this journey.
### IBM Quantum: Tomorrow's computing today
Quantum starts here(opens in a new tab)
### Keep exploring
View all blogs

