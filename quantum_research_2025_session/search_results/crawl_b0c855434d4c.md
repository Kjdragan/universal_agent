---
title: "IBM lays out clear path to fault-tolerant quantum computing | IBM Quantum Computing Blog"
source: https://www.ibm.com/quantum/blog/large-scale-ftqc
date: 2025-06-10
description: "IBM has developed a detailed framework for achieving large-scale fault-tolerant quantum computing by 2029, and we’re updating our roadmap to match."
word_count: 3245
---

  * Quantum Research
  * Blog

# How IBM will build the world's first large-scale, fault-tolerant quantum computer
With two new research papers and an updated quantum roadmap, IBM® lays out a clear, rigorous, comprehensive framework for realizing a large-scale, fault-tolerant quantum computer by 2029.
Date
10 Jun 2025
Authors
Ryan Mandelbaum
Jay Gambetta
Jerry Chow
Tushar Mittal
Theodore J. Yoder
Andrew Cross
Matthias Steffen
Topics
ResearchSystemsError Correction & MitigationCommunity
Share this blog
IBM has the most viable path to realize fault-tolerant quantum computing. By 2029, we will deliver IBM Quantum Starling — a large-scale, fault-tolerant quantum computer capable of running quantum circuits comprising 100 million quantum gates on 200 logical qubits. We are building this system at our historic facility in Poughkeepsie, New York.
Watch our new video, 'Realizing large-scale, fault-tolerant quantum computing,' on YouTube.
In a new paper, now available on the arXiv1, we detail a rigorous end-to-end framework for a fault-tolerant quantum computer that is modular and based on the bivariate bicycle codes we introduced with our landmark 2024 publication in _Nature_ 2. Additionally, we’re releasing a second paper3 that details the first-ever accurate, fast, compact, and flexible error correction decoder — one that is amenable to efficient implementation on FPGAs or ASICs for real-time decoding. We’ve updated our roadmap to match, with new processors and capabilities that will pave the way to quantum advantage, Starling, and fault tolerance.
Watch the 2025 IBM Quantum Roadmap update on YouTube.
!2025 Development & Innovation Roadmap.jpg
2025 IBM Quantum Roadmap.
Since 2020, IBM has worked transparently along its quantum roadmap, laying out the steps required to realize useful quantum computing. Recent revisions to that roadmap project a path to 2033 and beyond, and so far, we have successfully delivered on each of our milestones. Based on that past success, we feel confident in our continued progress.
In fact, from what we have seen, IBM is the only quantum computing organization in the world that will be capable of running quantum programs at the scale of hundreds of logical qubits and millions of quantum gates by the end of the decade.
What makes us so confident? Let us show you.
## Building a fault-tolerant quantum computer
Today, IBM is a leader in quantum computing. Our quantum computers are the only ones capable of delivering accurate results for quantum circuits with 5,000+ two-qubit gates. Based on research with partners such as RIKEN, Boeing, Cleveland Clinic, and Oak Ridge National Laboratory, we feel confident that our users will deliver quantum advantage — solving problems cheaper, faster, or more efficiently than classical alone — by the end of 2026, with quantum serving as an accelerator for classical HPC.
However, current devices and error-mitigating techniques limit us to small circuits. Unlocking the full promise of quantum computing will require a device capable of running larger, deeper circuits with hundreds of millions of gates operating on hundreds of qubits, at least. More than that, it will require a device capable of correcting errors and preventing them from spreading throughout the system. In other words, it will require a fault-tolerant quantum computer.
Watch 'Building the world’s first fault-tolerant quantum computer in Poughkeepsie, New York' on YouTube to preview our plans for the IBM Quantum Data Center in Poughkeepsie, NY.
In our new paper1, we detail six essential criteria for realizing a scalable architecture for reliable, large-scale quantum computing, and we show how our “bicycle architecture” meets these criteria. They are as follows:
  1. **Fault-tolerant.** Logical errors are suppressed enough for meaningful algorithms to succeed.
  2. **Addressable.** Individual logical qubits can be prepared or measured throughout the computation.
  3. **Universal.** A universal set of quantum instructions can be applied to the logical qubits.
  4. **Adaptive.** Measurements are real-time decoded and can alter subsequent quantum instructions.
  5. **Modular.** The hardware is distributed across a set of replaceable modules connected quantumly.
  6. **Efficient.** Meaningful algorithms can be executed with reasonable physical resources.

Below, we’ll lay out our architecture in more detail and explain how it meets these criteria. But before we do that, let’s briefly review how we detect and correct errors that arise in quantum computers.
## Correcting errors
Quantum error correction is the name for a family of techniques where we encode quantum information into physical qubits to protect them against errors. We do something similar in conventional computing. If we have three physical transistors and want to encode one binary digit's worth of information into them, then we could represent 0 as 000, and we could represent 1 as 111. We can define correction as majority voting — so even if one transistor errors, the encoded data isn’t corrupted. Let’s call 000 and 111 our three physical bits, and call the 0 and 1 they represent our logical bits.
Our goal is to do something similar in quantum computing — construct logical quantum bits, or qubits, from physical qubits. A physical qubit is a unit of well-isolated quantum computing hardware capable of being programmed and coupled to more than one other qubit in a controllable manner. A logical qubit is a qubit’s worth of encoded information that can be made from one or more physical qubits, depending on the quantum error correction code.
!IBM_FTQC_Logical-Qubit-3_2K.gif
The information of a logical qubit is encoded across many physical qubits to make it more resilient against errors.
We denote a quantum code’s parameters by [[n, k, d]] where n is the number of physical data qubits required, k is the resulting number of logical qubits, and d is the distance of the code — how many errors it takes to silently corrupt the data encoded on the logical qubit (i.e., how many errors it takes to change an error-free encoded state to another encoded state that appears completely error-free). In a classical analog, you’d say the above code (0 = 000, 1 = 111) is a [3, 1, 3] code (note the single bracket for a classical code). An error correction code can correct up to (d-1)/2 errors and detect up to d-1 errors.
Just like classical computing, we can represent 0 and 1 as specific quantum states that incorporate multiple qubits. You might encode |00⟩ as |0000⟩+|1111⟩, |01⟩ as |1100⟩+|0011⟩, |10⟩ as |1010⟩+|0101⟩ and |11⟩ as |1001⟩+|0110⟩, for example. Then, we can monitor these qubits by regularly running error syndrome extraction circuits, which detect evidence of errors — for example, measuring an output with an odd number of 1s in the case described above would be an obvious error. Together, these make up what we call the quantum memory.
But you need more than a memory to compute. Quantum computing requires a universal gate set, or a set of logic gates to which every quantum computation can be reduced. Our universal gate set begins with a group of familiar gates called Clifford gates, which must run on the encoded information quickly and with limited overhead. It also requires at least one non-Clifford gate, such as the T gate, which is harder to realize. We apply T gates by creating special states called “magic states” on helper qubits, then we entangle these qubits into our circuits with Clifford gates.
We also need to be able to read out the logical qubits — for this, we use a tool called the decoder. This is classical hardware capable of reading the error syndromes, updating our beliefs about errors in real time, and outputting the corrected information. Finally, this whole system must be modular — it must scale to sizes large enough to run meaningful computations.
## An architecture for realizing fault-tolerant quantum computing
Our new paper1 presents an architecture based on years of prior work, which meets these requirements for fault-tolerant quantum computing in a scalable system.
!ftqc-architecture-gray10-2.jpeg
Our fault-tolerant modular architecture is based on the bivariate bicycle code developed by IBM.
Let’s walk through each step of that architecture. In 2024, we introduced a fault-tolerant quantum memory2 based on quantum low-density parity check (qLDPC) codes called bivariate bicycle (BB) codes. The [[144,12,12]] gross code encodes 12 logical qubits into 144 data qubits—a gross—along with another 144 syndrome check qubits, for a total of 288 physical qubits. This code corrects errors just as well as the surface code does, but requires 10x fewer qubits to do so.
!IBM_FTQC_Torus_2K_v3.gif
The gross code developed by IBM encodes 12 logical qubits into 144 data qubits, or a 'gross' of data qubits, along with an additional 144 syndrome check qubits. Achieving this on the 2D surface of a quantum chip requires long-range connections between distant qubits within the chip — connections which follow the symmetry of a 3D torus.
Last year, our team and their collaborators discovered that we could build efficient, fault-tolerant logical processing units (LPUs) for qLDPC codes4 , 5. These LPUs are based on a technique called generalized (lattice) surgery6 and have valuable properties: they perform logical measurements using low-weight checks, and do so with very few additional qubits. We can use LPUs together with the symmetries of the qLDPC code to perform logical stabilizer computations, such as Clifford gates, state preparations, and measurements. In our new paper1, we design efficient LPUs for the gross code and a larger [[288,12,18]] BB code called the two-gross code that corrects more errors. The combined memory and LPU is one type of module in our architecture.
Our team and their collaborators also introduced concepts for universal adapters, which use bridges to interact and move logical quantum information between modules4 , 7. Parts of the adapters can be implemented using the inter-module microwave l-couplers we first demonstrated last year with IBM Quantum Flamingo. Our new paper1 elaborates on the adapter construction and characterizes a baseline inter-module measurement instruction.
!IBM_FTQC_L-Coupler_Still_4K.png
L-couplers enable long-range connections between qubits on separate quantum chips.
Universal computation can be done by augmenting logical stabilizer computations with magic state factories that create, distill, and consume magic states to apply universal gates. Sergey Bravyi and Alexei Kitaev invented the process of magic state distillation in 20048. Our team has made numerous contributions to the development of magic state preparation protocols since then, and in 2024, we published an experimental demonstration9 of such a protocol. Our new paper constructs explicit universal fault-tolerant instruction sets using adapters and magic state factory modules, and presents a compilation strategy adapted to the constraints of the bicycle architecture. This enables all of the operations we need for universal quantum computing.
!IBM_FTQC_Modular-Architecture-2_2K.gif
New paper details a modular fault-tolerant architecture with magic state factory.
The last step is an error correcting decoder, which we will introduce in the Starling proof-of-concept slated for 2028. Alongside our architecture paper, we present the first decoder architecture3 that is accurate, fast, flexible, and compact. It can fit on an FPGA or ASIC, classical components that are ubiquitous today. This decoding technique, called Relay-BP, achieves a 5x-10x reduction over other leading decoders, and shows that we do not need to use large amounts of HPC to perform the decoding required for fault-tolerant quantum computations.
## The IBM roadmap to fault-tolerant quantum computing
Driving our confidence is important theoretical work that demonstrates our ability to hit each of these milestones — and a roadmap to realize it. This year, we’ve presented an even more detailed version of the IBM Quantum Innovation Roadmap, laying out a timeline to build the critical components required for Starling, introduced in each successive bird.
!2025 Innovation Roadmap.jpg
2025 IBM Quantum Innovation Roadmap.
First, the gross code requires more connectivity than our chips currently have — so in 2025, we're building **IBM Quantum Loon** , a quantum chip with more connectivity and the architecture to enable proof-of-concept experiments toward high-rate qLDPC codes such as these. This includes c-couplers, connectors that can couple qubits more distant than their nearest neighbors.
!c-coupler.gif
C-couplers enable long-range connections between distant qubits within a quantum chip.
Then there’s the LPU and universal adapters. **IBM Quantum Kookaburra** , scheduled on our roadmap for 2026, will be the first quantum processor module capable of storing information in a qLDPC memory and processing it with an attached LPU. Meanwhile, **IBM Quantum Cockatoo** , which sits on our roadmap for 2027, will allow us to demonstrate entanglement between these modules with the universal adapter.
!ftqc-roadmap-gray10.jpeg
Over the next two years, processors outlined on the IBM Quantum Innovation Roadmap will demonstrate technologies that are essential for realizing Starling, our first fault-tolerant quantum computer.
This all comes together with **Starling** , the system slated for construction in Poughkeepsie, New York. In 2028, Starling will demonstrate the use of magic state injection with multiple modules. In 2029, Starling will scale to a system capable of running one hundred million gates on 200 logical qubits.
!IBM Quantum-Starling-01-Watermark.jpg
Render of IBM Quantum Starling.
Now, while we’re confident in our plans to deliver fault-tolerance by 2029, we expect to achieve quantum advantage sooner—by 2026. We’ve laid out the tools needed to realize and extend quantum advantage with the updated IBM Quantum Development Roadmap, and we are working to ensure that advantages realized before 2029 will run seamlessly on the fault-tolerant quantum computers of 2029 and beyond. Waiting until 2029 to pursue quantum computing could cause companies to fall behind those who start developing advantage-scale applications now.
!2025 Development Roadmap.jpg
2025 IBM Quantum Development Roadmap.
To accelerate the journey to advantage, we are excited to introduce **IBM Quantum Nighthawk** , a new processor slated for release later this year. Nighthawk will introduce a 120-qubit square lattice. Much like its predecessor, IBM Quantum Heron, it will be capable of running quantum circuits with 5,000 gates. However, a square lattice enables more qubit connectivity than the heavy hex lattice in Heron. Each qubit in a square lattice is directly connected to four nearest-neighbor qubits, versus two or three in a heavy hex lattice. Higher connectivity will allow Nighthawk to deliver roughly 16x the effective circuit depth of Heron, enabling our clients and users to run much more complex circuits.
We believe Nighthawk will be the platform for exploring the first cases of true quantum advantage, and we will work continuously to improve its quality and connectivity. By 2028, Nighthawk will be able to run circuits with 15,000 gates, and we’ll be able to connect up to 9 modules with l-couplers to realize 1,080 connected qubits.
!nighthawk.png
From 2025 to 2028, successive releases of Nighthawk will enable the exploration of increasingly complex quantum circuits.
Software is just as important in the journey to advantage. Our updated roadmap uses the Qiskit Runtime engine to improve the scalability of dynamic circuits, and new tools to benchmark use cases and extend them for quantum advantage. It also introduces better error mitigation tools to enable more complex workloads, and utility mapping tools designed to facilitate algorithm discovery for quantum advantage. Other upcoming software advances focus on orchestrating quantum and HPC resources — and we’re excited to be introducing a new C API that will allow more direct integrations of Qiskit into HPC environments.
_Watch the**2025 IBM Quantum Roadmap update** video on YouTube to learn more about these upcoming releases._
## The IBM legacy in Poughkeepsie, NY
!Bldg701_Facade_C-1950s.jpg
IBM Poughkeepsie, 1950s.
Starling is slated for construction at one of the most storied locations in the history of computing technology, home to a variety of firsts and a producer of world-leading machinery since its inception in 1941.
The IBM Poughkeepsie Lab quickly became an important site of computer production and computing advances. IBM produced its Electromatic Typewriter here in 1944 and a variety of electrical accounting equipment through the 1960s. IBM built the 701, its first commercial computer, here in 1952. Poughkeepsie also produced the extremely successful System/360 mainframe computer in the 1960s, and increasingly powerful mainframe computers in the decades to follow.
!5036_IBM604_C-late 1940s.jpg
IBM Poughkeepsie, 1940s.
!S360 Testing Poughkeepsie_Model 40-1960s.jpg
IBM Poughkeepsie, 1960s.
Poughkeepsie continues to serve as one of the main IBM manufacturing facilities in the U.S. The IBM Quantum Data Center lives there today, hosting the world’s most powerful quantum computers accessible via tiered access plans on IBM Quantum Platform. These quantum computers will be able to deliver quantum advantage by the end of 2026 with the help of the classical high-performance computing (HPC) community.
!IBM-Quantum_POK-2025_Isometric_PPL_Annotations_8-Bar.png
Render of Poughkeepsie data center with IBM Quantum System Two, Starling, and Blue Jay.
!IBM Quantum-Starling-02-Watermark.jpg
Render of IBM Quantum Starling.
!IBM Quantum-POK-02-People-2_16-9.png
Photograph of IBM Quantum System Two in the Poughkeepsie data center, 2025.
## Achieving results with quick cycles of learning
Altogether, our industry-leading expertise in superconducting qubits, control electronics and cryogenics, plus error correction theory and fault tolerance protocols, will allow our clients and partners to demonstrate advantage and will enable us to build a fault-tolerant quantum computer by the end of the decade. In just a year since its publication, the paper introducing our new error correction code2 has already received more than 200 citations, some by our competitors also working to realize quantum error correction. We have already delivered on the previous promises of our roadmap, and if we continue delivering along our roadmap, then we will realize fault-tolerant quantum computing on time.
Undergirding our scientific leadership are our quick cycles of learning. Using our agile hardware development process and leadership in semiconductor manufacturing, we are able to bring new chips from the drawing board to production on a timescale of months while also running numerous experiments in parallel, accelerating cycles of learning toward next-generation systems. We take lessons learned and quickly implement them in updated revisions. Additionally, we have a platform that has taught us to scale modularly with IBM Quantum System Two, and a data center prepared to accommodate the unique requirements of a fault-tolerant quantum computer.
And we’re not doing this alone. We’re partnering with institutions and businesses around the world to build the supply chain and ecosystem of hardware providers and software developers required to realize this system. We’re also partnering with members of our IBM Quantum Network to develop quantum applications for their domain-specific use cases.
We feel confident that our roadmap presents the most viable plan for bringing large-scale, fault-tolerant, and _useful_ quantum computing to the world. We hope you’ll join us along that path.
References
  1. Yoder, Theodore J., et al. _Tour de gross: A modular quantum computer based on bivariate bicycle codes._ arXiv:2506.03094, arXiv, 3 Jun 2025. arXiv.org, <https://www.arxiv.org/abs/2506.03094>.
| ↩
  2. Bravyi, Sergey, et al. _High-threshold and low-overhead fault-tolerant quantum memory._ DOI:10.1038/s41586-024-07107-7, Nature, 27 Mar 2024. nature.com, <https://www.nature.com/articles/s41586-024-07107-7>.
| ↩
  3. Müller, Tristan, et al. _Improved belief propagation is sufficient for real-time decoding of quantum memory._ arXiv:2506.01779, arXiv, 2 Jun 2025. arXiv.org, <https://arxiv.org/abs/2506.01779>.
| ↩
  4. Cross, Andrew, et al. _Improved QLDPC Surgery: Logical Measurements and Bridging Codes._ arXiv:2407.18393, arXiv, 25 Jul 2024. arXiv.org, <https://arxiv.org/abs/2407.18393>.
| ↩
  5. Williamson, Dominic J. & Yoder, Theodore J. _Low-overhead fault-tolerant quantum computation by gauging logical operators._ arXiv:2410.02213, arXiv, 3 Oct 2024. arXiv.org, <https://arxiv.org/abs/2410.02213>.
| ↩
  6. Cohen, Lawrence Z., et al. _Low-overhead fault-tolerant quantum computing using long-range connectivity._ DOI:10.1126/sciadv.abn1717, Science Advances, 20 May 2022. arXiv.org, <https://www.science.org/doi/10.1126/sciadv.abn1717>.
| ↩
  7. Swaroop, Esha, et al. _Universal adapters between quantum LDPC codes._ arXiv:2410.03628, arXiv, 19 Mar 2025. arXiv.org, <https://arxiv.org/abs/2410.03628>
| ↩
  8. Bravyi, Sergei & Kitaev, Alexei. _Universal Quantum Computation with ideal Clifford gates and noisy ancillas._ arXiv:quant-ph:0403025, arXiv, 4 Mar 2004. arXiv.org, <https://arxiv.org/abs/quant-ph/0403025>.
| ↩
  9. Gupta, Riddhi S., et al. _Encoding a magic state with beyond break-even fidelity._ DOI:10.1038/s41586-023-06846-3, Nature, 10 Jan 2024. nature.com, <https://www.nature.com/articles/s41586-023-06846-3>.
| ↩

### IBM Quantum: Tomorrow's computing today
Quantum starts here(opens in a new tab)
### Keep exploring
View all blogs

