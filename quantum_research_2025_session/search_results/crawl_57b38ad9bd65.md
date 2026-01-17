---
title: "Quantum computing for quantum chemistry: a brief perspective | PennyLane Blog"
source: https://pennylane.ai/blog/2021/11/quantum-computing-for-quantum-chemistry-a-brief-perspective
date: 2021-11-24
description: "We share two short lessons regarding the leading quantum algorithms for quantum chemistry: the variational quantum eigensolver and quantum phase estimation."
word_count: 2506
---

Help us shape the future of PennyLane by taking a few minutes to share your thoughts on our quantum programming survey. Take the survey!
  1. Blog/
  2. Quantum Chemistry/
  3. Quantum computing for quantum chemistry: a brief perspective

November 24, 2021
# Quantum computing for quantum chemistry: a brief perspective

Telling stories makes us human, and we can tell a compelling story about quantum computing. It goes something like this:
_The universe is quantum. To understand it and harness its powers we need to embrace quantum mechanics as a core principle for new technologies. This is the promise of quantum computers: they are the most powerful simulators possible and building them will revolutionize computation forever._
This is an appealing message that is easy to believe, but it doesn’t explain exactly why quantum computers are the ultimate simulators, nor does it specify precisely how they can revolutionize computations. The reality is that even quantum computers have limitations in their capabilities, and we are still uncovering the most efficient quantum algorithms to solve important problems, while understanding how they compare to the best existing methods.
In this post, we share two short lessons learned while thinking deeply about quantum algorithms. They motivate a strategic focus of the quantum algorithms team at Xanadu to think critically and with a long-term perspective. Our goal is to deeply understand the most promising directions for future research in quantum algorithms. We focus specifically on quantum chemistry, a field of industrial importance that is fundamentally linked with understanding the quantum properties of matter. We share insights on the role that quantum computers play as the ultimate simulators by reflecting on arguably the leading quantum algorithms for quantum chemistry: the variational quantum eigensolver and quantum phase estimation.
**Lesson 1** : _The variational quantum eigensolver algorithm faces a fundamental scalability challenge, the measurement problem, that undermines its potential to tackle problems beyond the reach of existing classical methods_.
To extract information from a quantum state, for example its energy, variational algorithms need to perform a number of estimates that grows with the number of parameters in the Hamiltonian describing the system. To optimize a quantum circuit, at least one such information-extraction step has to be performed for each circuit parameter. The number of Hamiltonian parameters and the number of circuit parameters both scale rapidly with system size, leading to an overall cost that becomes prohibitive for larger systems, especially when high accuracy is required. This is compounded by the inherent difficulty of achieving high-precision calculations through sampling statistical estimators. These issues are fundamental — solving them requires new algorithms.
**Lesson 2** : _The best fault-tolerant quantum algorithms can perform high-accuracy electronic structure calculations without the need for approximations. They use resources that scale similarly to the best classical methods, which in contrast rely on approximations and cannot guarantee the high accuracy that quantum algorithms achieve_.
It has been known for years that advanced quantum algorithms based on quantum phase estimation can perform electronic structure calculations in sub-exponential time with accuracy that rivals exact diagonalization methods. Moreover, they can do this generally for any system given only an input Hamiltonian. This guarantee of simultaneously achieving high accuracy, efficiency, and generality is a feat that is believed to be impossible for classical algorithms.
However, despite their sub-exponential scaling, the precise cost of early versions of quantum algorithms was concerning, casting doubts on their practicality. Recent advances in quantum algorithms such as qubitization [1] and first-quantization methods with optimized compilation [2] have greatly reduced resource requirements to the point that they are comparable to classical methods. These algorithms are not heuristics — they are guaranteed to achieve their stated capabilities.
These two insights paint a clear picture: we should continue to innovate on designing better quantum algorithms, with a long-term perspective. For variational algorithms, we will benefit from thinking beyond their usual formulations and invent new strategies that can address their fundamental obstacles. For algorithms based on quantum phase estimation, there is significant room for further reducing resource requirements and for tailoring them to specific application areas.
In the content below, we expand on these two lessons, beginning with a summary of fault-tolerant quantum algorithms for quantum chemistry. These sections are more technical as we explore the details that support the lessons shared above.
## Quantum computers are the ultimate simulators for quantum chemistry
Quantum phase estimation is the main method in fault-tolerant quantum algorithms for quantum chemistry. It is arguably the most powerful primitive in all of quantum computing, and it is also used as a subroutine in Shor’s factoring algorithm. The task is the following, we are given:
  1. A quantum circuit that can implement a unitary operator , and
  2. An eigenstate of such that .

The quantum phase estimation algorithm can compute with error using calls to the circuit implementing . Besides the qubits encoding the input state , the algorithm uses additional qubits that are measured to reveal a binary encoding of .
If instead we are given an input state that is not an eigenstate, quantum phase estimation will project the input qubits to a particular eigenstate with probability and compute the corresponding eigenvalue . The algorithm thus more generally samples the eigenvalues of with respect to a distribution induced by the input state.
Many properties of molecules and materials can be understood by calculating the eigenvalues of their corresponding Hamiltonian , in particular its smallest eigenvalue, the ground-state energy. In quantum algorithms based on quantum phase estimation, the Hamiltonian is encoded into a unitary operator such that the eigenvalues of are functions of the eigenvalues of . For example, a simple but suboptimal way to do this is through the time-evolution operator . Using to denote the eigenvalues of , the eigenvalues of the time-evolution operator are simply .
A more advanced encoding method is a technique known as qubitization [1]. It starts by expressing the Hamiltonian as a linear combination of unitaries
where is a unitary and . We also define the preparation operator acting on auxiliary qubits, which performs the transformation
where
Finally, we define the selection operator
Then the qubitization operator
has eigenvalues equal to . The operator is unitary and acts on the system qubits and on the auxiliary qubits that encode the states . The remarkable part of this construction is that the operator can be implemented exactly on a quantum computer and using fewer resources than previous encoding methods.
Quantum phase estimation can be used to estimate the phase and recover the ground-state energy , provided that we have access to an input state with sufficiently large overlap with the true ground state. Techniques like the Hartree-Fock method can be used to efficiently define an approximate ground state. The cost of initial state preparation is important, but even in first-quantization it is estimated to require fewer resources than the rest of the algorithm.
To estimate with precision , we need to estimate with precision . The quantity can be interpreted as a norm of the Hamiltonian, capturing the energy scale of the problem. Overall this means that we must make a total of calls to the qubitization operator.
How difficult is it to implement ? One recent work studying first-quantization techniques [2] showed that up to logarithmic factors, implementing requires only gates, where is the number of particles. This is remarkable, and it exemplifies the impact of qubitization, first-quantization techniques, and optimized compilation. By carefully analyzing the value of in terms of the number of particles and the number of orbitals , the total cost of the quantum algorithm to compute the ground-state energy of a Hamiltonian with accuracy is asymptotically
up to logarithmic factors. The important point here is that the degree of this polynomial scaling is small, and the complexity is only inversely proportional to the error in the estimation.
Careful estimates of the precise cost of simulation were performed in [2] for various molecules, including the electrolyte lithium hexafluorophosphate consisting of 72 electrons. The authors estimated that roughly Toffoli gates are required to compute its ground-state energy with chemical accuracy using thousands of plane waves, and Toffoli gates are needed when using millions of plane waves. In fault-tolerant schemes, Toffoli gates are the slow and expensive gates that capture the cost of the quantum computation.
The actual runtime will depend on the clock rate of the quantum computer. Suppose that the quantum computer can fault-tolerantly implement Toffoli gates at an optimistic rate of 100 MHz. Then the molecule could be simulated in times ranging from 8 seconds to ~3.5 hours depending on the number of plane waves used. Continued efforts on quantum algorithms can help bring these values further down. Of course, these are only estimates, and further complications may arise when analyzing these algorithms in more detail. Nonetheless, they are helpful in quantifying what quantum computing can do for quantum chemistry as we continue to strive towards fault-tolerance.
## Scaling VQE: a back-of-the-envelope calculation
A standard argument for the merits of quantum algorithms for quantum chemistry is that the state of a system with spin-orbitals can be represented using only qubits, whereas classically an exponentially-large amount of memory is required. But even with access to a quantum state it is still necessary to extract information from it, for example by computing an expectation value. This isn’t always easy.
In the variational quantum eigensolver (VQE) algorithm, a parametrized circuit is optimized to minimize the expectation value of a Hamiltonian. On paper, we could perform a measurement in the eigenbasis of , but this requires knowledge of the transformation that diagonalizes it, which is at least as hard as computing the ground-state energy. Instead, to avoid adding complexity to the circuit, variational algorithms express the Hamiltonian as a linear combination of operators that are products of single-qubit unitaries:
and compute the expectation value term-by-term as
The expectation values can then be calculated by adding only one layer of single-qubit rotations.
To train the quantum circuit, we need to further compute an update rule for each of its parameters. Regardless of the optimization method, during the entire optimization run each parameter must be updated at least once, which requires computing the cost function . This means that we need to compute at least as many expectation values as there are parameters in the circuit.
Assume that we want to perform one optimization step updating all circuit parameters by computing the corresponding expectation values with error . How many samples from the circuit do we need? We can get a good idea from a simple back-of-the-envelope calculation.
Let be the number of terms in the Hamiltonian, the number of gates in the circuit, and the number of qubits. Estimating an expectation value through direct sampling with error (captured by the standard deviation of the estimator) requires
samples. This is a consequence of the square-root law in statistical estimation. In our case, we actually need to compute the sum of expectation values. During training, this may be alleviated by subsampling terms in the Hamiltonian weighted by their coefficients, in which case we can think of as quantifying the number of sufficiently-large terms that need to be computed. To achieve a total error , assuming these correspond to independent variables, each term must be estimated with error , leading to a scaling of . Each such estimate has to be repeated for all terms, bringing the number of samples to
Finally, we need to perform this estimation for each gate in the circuit, leading to a total of
samples. How do and scale with the number of qubits ? A very optimistic estimate is to hope that the number of terms in the Hamiltonian scales linearly as , and that the number of gates also scales linearly as . This leads to a number of samples
which is already a worse asymptotic scaling than the fault-tolerant algorithms discussed above, especially for high-accuracy calculations that require a large number of qubits.
This estimate is likely too optimistic. In second-quantization, where we can use optimized molecular orbitals to reduce the value of for a given number of particles, a Hamiltonian has terms. Clever factorization techniques can bring this down to . Similarly, for a circuit capable of generating high-quality approximations to Hamiltonian eigenstates, a more accurate estimate is to assume a scaling of at least for the number of gates, in which case the number of samples would scale as
On top of this, we expect additional resources to perform all the required steps for fully optimizing the circuit.
These are only rough approximations using simple methodologies, but hopefully they convey the information that high-accuracy estimation of expectation values is difficult. More concrete resource estimates have been performed in the literature, reaching similar conclusions. For example, one paper [3] estimates that performing a high-accuracy calculation of a single expectation value, i.e., without including the full cost of optimization, requires around 1.9 days for the methane molecule and 71 days for ethanol.
There are several clever techniques that can be used to improve scaling, for example updating parameters in batches, subsampling terms in the Hamiltonian during training, lowering accuracy targets during training, or using Bayesian techniques for more efficient parameter estimation. Other methods have also been introduced recently, see for example [4] and [5]. It is crucial to develop these techniques and to incorporate them in existing workflows, but the fact remains that computing expectation values is very challenging and it is a major obstacle for the variational quantum eigensolver.
## Conclusion
If we can build fault-tolerant quantum computers, we can achieve a new paradigm of simulation for quantum chemistry. This is one of the most difficult technological challenges that humans have ever pursued and there are no guarantees that we will be successful. But if we get there, there is a mathematical foundation that guarantees that we can simulate important properties of molecules and materials with higher accuracy than ever before, potentially with similar resources than existing classical methods. To unlock this capability, it is crucial to hold a long-term perspective and to deeply understand the scaling and cost of quantum algorithms. This requires both a systematic improvement of existing methods and continued innovation to develop better ones. It is an exciting time for quantum algorithms research.
## References
[1] G.H. Low and I. Chuang, Hamiltonian simulation by qubitization.
[2] Y. Su, D. Berry, N. Wiebe, N. Rubin, and R. Babbush, Fault-tolerant quantum simulations of quantum chemistry in first quantization
[3] J.F. Gonthier, M.D. Radin, C. Buda, E.J. Doskocil, C.M. Abuan, and J. Romero, Identifying challenges towards practical quantum advantage through resource estimation: the measurement roadblock in the variational quantum eigensolver
[4] Amara Katabarwa, Alex Kunitsa, Borja Peropadre, Peter Johnson, Reducing runtime and error in VQE using deeper and noisier quantum circuits
[5] Hsin-Yuan Huang, Richard Kueng, John Preskill, Efficient estimation of Pauli observables by derandomization
## About the author

Making quantum computers useful
 _Last modified:_  _August 06, 2024_
### Related Blog Posts

  * 1
  * 2
  * 3
  * 4
  * 5
  * 6
  * 7
  * 8

