---
title: "Demonstrating a universal logical gate set in error-detecting surface codes on a superconducting quantum processor | npj Quantum Information"
source: https://www.nature.com/articles/s41534-025-01118-6
date: unknown
description: "Fault-tolerant quantum computing (FTQC) is essential for achieving large-scale practical quantum computation. Implementing arbitrary FTQC requires the execution of a universal gate set on logical qubi"
word_count: 7624
---

## Your privacy, your choice
We use essential cookies to make sure the site can function. We also use optional cookies for advertising, personalisation of content, usage analysis, and social media, as well as to allow video information to be shared for both marketing, analytics and editorial purposes.
By accepting optional cookies, you consent to the processing of your personal data - including transfers to third parties. Some third parties are outside of the European Economic Area, with varying standards of data protection.
See our privacy policy for more information on the use of your personal data.
Manage preferences for further information and to change your choices.
Accept all cookies Reject optional cookies
Skip to main content
Thank you for visiting nature.com. You are using a browser version with limited support for CSS. To obtain the best experience, we recommend you use a more up to date browser (or turn off compatibility mode in Internet Explorer). In the meantime, to ensure continued support, we are displaying the site without styles and JavaScript.
Demonstrating a universal logical gate set in error-detecting surface codes on a superconducting quantum processor 
 Download PDF 
 Download PDF 
## Abstract
Fault-tolerant quantum computing (FTQC) is essential for achieving large-scale practical quantum computation. Implementing arbitrary FTQC requires the execution of a universal gate set on logical qubits, which is highly challenging. Particularly, in the superconducting system, two-qubit gates on surface code logical qubits have not been realized. Here, we experimentally implement a logical CNOT gate along with arbitrary single-qubit rotation gates on distance-2 surface codes using the superconducting quantum processor _Wukong_ , thereby demonstrating a universal logical gate set. In the experiment, we demonstrate the transversal CNOT gate on a two-dimensional topological processor based on a tailored encoding circuit, at the cost of removing the ancilla qubits required for stabilizer measurements. Furthermore, we fault-tolerantly prepare logical Bell states and observe a violation of CHSH inequality, confirming the entanglement between logical qubits. Using the logical CNOT gate and an ancilla logical state, arbitrary single-qubit rotation gates are realized through gate teleportation. All logical gates are characterized on a complete state set and their fidelities are evaluated by logical Pauli transfer matrices. The demonstration of a universal logical gate set and the entangled logical states highlights significant aspects of FTQC on superconducting quantum processors.
### Similar content being viewed by others

###  Logical-qubit operations in an error-detecting surface code 
Article 16 December 2021

###  Experimental fault-tolerant code switching 
Article 24 January 2025

###  Logical quantum processor based on reconfigurable atom arrays 
Article Open access 06 December 2023
## Introduction
Quantum computing holds the promise to accelerate classical computing in various applications such as large number factorization1."), quantum simulation2."), and machine learning3."). However, physical qubits are typically very fragile and are easily disturbed by environmental noise. To address the noise issues in large-scale quantum computing, quantum error correction techniques have been proposed, which introduce redundant information and encode quantum states onto logical qubits to ensure fault tolerance4."),5."),6.").
In recent years, multiple experiments across various quantum computing platforms have demonstrated the memory of quantum information on logical qubits. These experiments are based on hardware systems encompassing superconducting7."),8."),9."),10."),11."),12."),13."),14."),15."), ion trap16."),17."), neutral atom18."), and other systems19."),20."),21."),22."),23."). Particularly in experiments using bosonic codes, it has been demonstrated that the quality of logical qubits can exceed the so-called break-even point21."),22."), validating the effectiveness of quantum error correction techniques in suppressing quantum noise.
Furthermore, to achieve fault-tolerant quantum computing (FTQC), a set of logical gates needs to be implemented. The simplest approach to implement logical gates is transversally, where all physical qubits have interacted with at most one physical qubit from each logical block, therefore naturally ensuring fault-tolerance. However, a well-known theorem states that no quantum code can simultaneously promise a transversal and universal logical gate set24."),25."),26."). For instance, in the surface code, the CNOT gate is transversal. While some single-qubit rotation gates, such as the _S_ gate and _T_ gate, typically need to be implemented indirectly using gate teleportation circuits with ancilla logical states27."),28.").
Currently, more and more experimental works are focusing on demonstrations of logical gates of various quantum error correction codes12."),14."),18."),29."),30."),31."),32."),33."),34."),35."),36."),37."). For instance, in neutral atom systems, demonstrations of the CNOT, CZ, and CCZ gates have been achieved on the [8,3,2] color code18."). In ion trap systems, the _H_ , _S_ , _T_ , and CNOT gates have been demonstrated on the Steane code30."), forming a universal gate set. In superconducting systems, experimental demonstrations of logical gates remain limited, particularly for the surface code, which is the most promising encoding scheme due to its high theoretical threshold and practical nearest-neighbor connectivity requirements28."),38."). Ref. 31.") demonstrated a universal set of single-qubit gates on the distance-2 surface code in superconducting systems, showing the potential of using surface code logical qubits for FTQC in the superconducting quantum processor. The main limitation of their work is the lack of two-qubit logical operations, thus not constituting a complete universal gate set. Additionally, the ancilla quantum states used in gate teleportation are physical states rather than logical states, which is inconsistent with the requirements in FTQC. To the best of our knowledge, no work has yet implemented a complete universal set of logical gates in either the superconducting system or the surface code encoding.
In our work, we use the error-detecting surface code with distance 2 (Fig. 1a) to implement a complete set of universal logical gates, including arbitrary single-qubit rotations around the _Z_ or _X_ axis and the CNOT gate, filling the gap in current literature. In the experiment, we encode two logical qubits in a 2 × 4 qubit region of the superconducting quantum processor _Wukong_ (see Fig. 1b and Supplementary Note 1). The logical CNOT gate is implemented transversally, i.e., by performing four CNOT gates between the corresponding physical qubits. Additionally, single-qubit rotation gates are implemented by preparing the ancilla logical states and applying gate teleportation circuit, which consists of a logical CNOT gate and logical _X_ or _Z_ measurement on the ancilla qubit. To implement transversal CNOT gates on a two-dimensional topology, our design has to simplify the encoding of two logical qubits by removing the measurement qubits required for stabilizer measurements. The error detection in our experiment is achieved through measurement and post-selection at the end of the circuit. While no stabilizer measurements are performed after logical operations, any single error can still be detected in fault-tolerant circuits by reconstructing the stabilizers from the terminal measurement results.
**Fig. 1: Distance-2 surface code and qubit layout in the experiment.**

**a** Two logical qubits of the distance-2 surface code and transversal CNOT gate. Each logical qubit is encoded by four data qubits and the logical CNOT gate between two logical qubits corresponds to the four physical CNOT gates between the corresponding data qubits. **b** The experiment uses eight physical qubits arranged in a 2 × 4 rectangular region on the superconducting quantum processor _Wukong_. The deep blue lines represent the topology of the processor, indicating the allowed two-qubit gates between physical qubits.
Full size image
The logical Pauli transfer matrices (LPTMs) of these logical gates are characterized on a complete set of states, according to which the gate fidelities are evaluated and listed in Table 1. Using fault-tolerant logical state encoding circuits and transversal CNOT gates, four logical Bell states are also prepared. By verifying the violation of the CHSH inequality with these Bell states, we have confirmed the presence of quantum entanglement between two logical qubits. In the experiment, all fault-tolerantly prepared logical states, including single-qubit states and Bell states, exhibit higher fidelity than the results on the corresponding physical qubits (see Table 2).
**Table 1 Summary of the fidelities of logical gates (including characterization) in the experiment**
 Full size table
**Table 2 Comparison of the fidelities between fault-tolerant prepared logical states and physical states (including preparation and characterization) in the experiment**
 Full size table
Note that the fidelity referred to here is the overall fidelity of the preparation and characterization process, therefore, it does not indicate that a logical state beyond the break-even point has been achieved. However, as hardware improves, the logical error rate of error detection codes could exceed the breakeven point, as indicated by some theoretical and experimental work using error detection codes in the context of early fault-tolerant computing39."),40.").
Moreover, in the long term, the demonstration of transversal CNOT gates on surface codes could support more efficient FTQC. Theoretical works suggest that combining transversal CNOT gates with two-dimensional (2-D) operations has the potential to reduce the space-time overhead of FTQC on surface codes41."),42."). However, we recognize that this may be a rather distant goal for superconducting systems, as the transversal CNOT gate for surface codes typically requires a multi-layer architecture or a 2-D architecture with long-distance couplings43."),44."),45."),46."). Nonetheless, our experiment provides an early exploration for these intriguing applications.
## Results
### Logical state preparation and measurement
The logical qubit of distance-2 surface code is encoded on four data qubits and is capable of detecting any single-qubit errors. Its code space is the +1 eigenspace of the following stabilizer group:
$${\mathcal{S}}=\langle {X}_{1}{X}_{2}{X}_{3}{X}_{4},{Z}_{1}{Z}_{2},{Z}_{3}{Z}_{4}\rangle .$$
(1) 
Then the logical Pauli operators are defined as:
$${Z}_{L}={Z}_{1}{Z}_{3},\quad {X}_{L}={X}_{3}{X}_{4}.$$
(2) 
Accordingly, the explicit form of the logical state can be written as:
$$\begin{array}{rcl}\left\vert {0}_{L}\right\rangle &=&\frac{1}{\sqrt{2}}(\left\vert 0000\right\rangle +\left\vert 1111\right\rangle ),\\\ \left\vert {1}_{L}\right\rangle &=&\frac{1}{\sqrt{2}}(\left\vert 0011\right\rangle +\left\vert 1100\right\rangle ),\end{array}$$
(3) 
and
$$\left\vert {\pm }_{L}\right\rangle =\frac{1}{\sqrt{2}}(\left\vert {0}_{L}\right\rangle \pm \left\vert {1}_{L}\right\rangle ).$$
(4) 
Here, we designed circuits for preparing the logical states \\(\left\vert {0}_{L}\right\rangle\\), \\(\left\vert {1}_{L}\right\rangle\\), \\(\left\vert {+}_{L}\right\rangle\\) and \\(\left\vert {-}_{L}\right\rangle\\) fault-tolerantly (see Fig. 1), whose fault tolerance is proven in the Methods. In this error-detection context, an operation is fault-tolerant if any single error produces a non-trivial syndrome and can therefore be post-selected out. In order to simultaneously ensure fault-tolerant state preparation and transversal CNOT gate implementation between \\(\left\vert {\pm }_{L}\right\rangle\\) and \\(\left\vert 0/{1}_{L}\right\rangle\\) states, we adopt the qubit allocation scheme depicted in Fig. 2a and b. The key is that we exploit the property that \\(\left\vert {\pm }_{L}\right\rangle\\) can be decomposed into product states (\\(\left\vert {\pm }_{L}\right\rangle =\frac{1}{2}{(\left\vert 00\right\rangle \pm \left\vert 11\right\rangle )}^{\otimes 2}\\)), and encode \\(\left\vert {\pm }_{L}\right\rangle\\) on the leftmost two (q1 and q5) and the rightmost two physical qubits (q4 and q8) in the hardware. Moreover, we also provide a circuit for preparing arbitrary logical state \\(\left\vert {\psi }_{L}\right\rangle\\) in Fig. 2c. Generally, such a circuit for encoding arbitrary logical state is not fault-tolerant, nor is this circuit. In this way, a logical state can be encoded on a chain of four physical qubits (q1-q4) with only nearest-neighbor coupling.
**Fig. 2: Logical state preparation circuits and characterization.**

**a** , **b** Circuits for fault-tolerant (FT) preparation of \\(\left\vert 0/{1}_{L}\right\rangle\\) and \\(\left\vert {\pm }_{L}\right\rangle\\) states. The \\(\left\vert {1}_{L}\right\rangle\\) (or \\(\left\vert {-}_{L}\right\rangle\\)) state are obtained by applying _X_ _L_ (or _Z_ _L_) gate after preparing the \\(\left\vert {0}_{L}\right\rangle\\) (or \\(\left\vert {+}_{L}\right\rangle\\)) state. **c** Circuits for non-fault-tolerant (nFT) preparation of arbitrary logical state \\(\left\vert {\psi }_{L}\right\rangle\\). **d** –**f** Density matrices and fidelities of the six single logical states prepared in the experiment. All logical state density matrices are obtained through logical state tomography. **g** Comparison of fidelity and post-selection (PS) rates between experiments and simulations. The figure shows the fidelity of six logical states and the post-selection rates when measuring their eigenoperators (_Z_ _L_ or _X_ _L_).
Full size image
After preparing the logical states, logical _X_ , _Y_ , or _Z_ measurements are performed to characterize these states. Their measurement results are determined by the product of the corresponding Pauli operator measurement result on each data qubits. The logical _X_ and _Z_ measurements are fault-tolerant and correspond to measurements in the _X_ and _Z_ bases on all data qubits, respectively. Post-selection is carried out based on the conditions provided by the three generators of the stabilizer group, discarding results that violate these conditions. Specifically, assuming the _X_ or _Z_ measurement result on the _i_ th data qubit is \\({m}_{i}^{x}\\) or \\({m}_{i}^{z}\in \\{+1,-1\\}\\) the post-selection conditions are \\({m}_{1}^{x}{m}_{2}^{x}{m}_{3}^{x}{m}_{4}^{x}=+1\\), and \\({m}_{1}^{z}{m}_{2}^{z}=+1\\), \\({m}_{3}^{z}{m}_{4}^{z}=+1\\) for logical _X_ and _Z_ measurements, respectively. On the other hand, measurement of the logical _Y_ operator _Y_ _L_ =  _Z_ 1 _Y_ 3 _X_ 4 is not fault-tolerant. It requires _Z_ measurements on data qubits D1 and D2, a _Y_ measurement on D3, and an _X_ measurement on D4. The corresponding post-selection condition is \\({m}_{1}^{z}{m}_{2}^{z}=+1\\). In this case, post-selection cannot eliminate all single-qubit error cases but can suppress some of them. Define the probability of successfully passing the post-selection condition as the post-selection rate. Since the post-selection conditions vary under different measurement bases, the post-selection rate is significantly influenced by the measurement basis.
Here, we conduct experimental demonstrations and characterizations on the fault-tolerantly prepared \\(\left\vert 0/{1}_{L}\right\rangle\\), \\(\left\vert {\pm }_{L}\right\rangle\\) states, and non-fault-tolerantly prepared \\(\left\vert 0/{1}_{L}\right\rangle\\) states. Through logical quantum state tomography, we constructed the density matrix _ρ_ _L_ in the code space, as shown in Fig. 2d–f. Furthermore, we computed the fidelity of the logical state:
$${F}_{L}=\langle {\psi }_{L}| {\rho }_{L}| {\psi }_{L}\rangle ,$$
(5) 
where \\(\left\vert {\psi }_{L}\right\rangle\\) is the ideal logical quantum state. The fidelities of the fault-tolerantly prepared states \\(\left\vert {0}_{L}\right\rangle ,\left\vert {1}_{L}\right\rangle\\) and \\(\left\vert {+}_{L}\right\rangle ,\left\vert {-}_{L}\right\rangle\\), as well as the non-fault-tolerantly prepared states \\(\left\vert {0}_{L}\right\rangle\\) and \\(\left\vert {1}_{L}\right\rangle\\), are 97.9(2)%, 98.0(2)%, 97.7(2)%, 97.8(2)%, 89.2(3)%, and 88.9(3)%, respectively. We also computed the fidelities of the \\(\left\vert 0\right\rangle ,\left\vert 1\right\rangle\\) and \\(\left\vert +\right\rangle ,\left\vert -\right\rangle\\) states prepared on the eight physical qubits in the experiment using physical state tomography. For a fair comparison, we did not use readout error mitigation techniques47.") during the physical state tomography. The highest values among eight physical qubits are 96.9(3)% for \\(\left\vert 0\right\rangle\\) in q2, 94.8(4)% for \\(\left\vert +\right\rangle\\) in q2, 93.6(5)% for \\(\left\vert -\right\rangle\\) in q2 and 90.8(6)% for \\(\left\vert 1\right\rangle\\) in q3. All these values are lower than the fidelities of the fault-tolerantly prepared logical states, demonstrating the noise-suppressing effect in the overall process of the preparation and characterization. However, we remind readers that the fidelities of logical or physical states also affected by noise in the tomography protocol. Due to the difficulty in distinguishing noise in characterization from noise in state preparation, these results do not imply that the fidelity of logical state preparation exceeds that of the physical state. Especially given the significant readout noise on our superconducting processor, the contribution of error detection to the improvement in readout fidelity is likely more substantial.
In addition, we provide information on the post-selection rates when measuring the logical state eigenoperators in Fig. 2e (see Supplementary Note 3 for complete data on the post-selection rate). We also present simulation results for comparison, which are based on the Pauli depolarizing noise model, a commonly used error model in quantum error correction research (see details in Supplementary Note 4). However, we also remark that this model does not fully capture the real noise, leading to discrepancies between experimental and simulated data.
### Logical CNOT gate and Bell states
Next, our experiment demonstrates a transversal CNOT gate between two surface code logical qubits (see Fig. 3a and b). Initially, two logical states \\(\left\vert {\psi }_{L}\right\rangle\\) and \\(\left\vert {\varphi }_{L}\right\rangle\\), are prepared on two chains of the quantum processor (q1-q4 and q5-q8), where \\(\left\vert {\psi }_{L}\right\rangle\\) and \\(\left\vert {\varphi }_{L}\right\rangle\\) are from a complete state set \\(\\{\left\vert {+}_{L}\right\rangle ,\left\vert {-}_{L}\right\rangle ,\left\vert {0}_{L}\right\rangle ,\left\vert {i}_{L}\right\rangle \\}\\). Here \\(\left\vert {i}_{L}\right\rangle =(\left\vert {0}_{L}\right\rangle +i\left\vert {1}_{L}\right\rangle )/\sqrt{2}\\) is the +1 eigenstate of the logical operator _Y_ _L_. This step is realized by the preparation circuit for arbitrary logical states described in the previous section. Since the fidelity of states \\(\left\vert {+}_{L}\right\rangle\\) and \\(\left\vert {-}_{L}\right\rangle\\) in our experiment is higher, we prioritize selecting these two states to form the complete state set. The density matrices of the initial logical states are characterized by logical state tomography. Subsequently, a transversal CNOT gate is applied to the initial logical states, and the output states are characterized using logical state tomography. Based on the expectation values of two-qubit Pauli operators of the initial and output states, we extract the LPTMs using the method presented in ref. 31."). The fidelity of the logical CNOT gate, as computed from the LPTM, is found to be \\({F}_{L}^{G}=88.9(5) \%\\). Details concerning the LPTM and fidelity calculation are presented in Supplementary Note 2. Due to the noise in the characterization, this result is actually a conservative estimate of the logical gate fidelity.
**Fig. 3: Logical CNOT gate and Bell state characterization.**

**a** Circuit of the logical CNOT gate implemented transversally. **b** , **c** Circuit for applying a logical CNOT gate on arbitrary logical states \\(\left\vert {\psi }_{L}\right\rangle\\) and \\(\left\vert {\varphi }_{L}\right\rangle\\), and the circuit for fault-tolerant preparation of Bell states, respectively. The blocks represent logical state preparation circuits and the logical CNOT gate. The upper half of the logical CNOT block corresponds to the control logical qubit, while the lower half corresponds to the target logical qubit. **d** Density matrices and fidelities of the four logical Bell states prepared fault-tolerantly in the experiment. **e** Average fidelity and post-selection (PS) rates of four logical Bell states when measuring _X_ _L_ ⊗ _X_ _L_ , _Y_ _L_ ⊗ _Y_ _L_ and _Z_ _L_ ⊗ _Z_ _L_ in experiments and simulations.
Full size image
Then we use the logical CNOT gate to prepare four Bell states on logical qubits, which are important entangled resources in quantum information. Following the above initialization method, the control and target logical qubits can be initialized to \\(\left\vert {\pm }_{L}\right\rangle\\) and \\(\left\vert 0/{1}_{L}\right\rangle\\) states, respectively. Then they can be acted by a logical CNOT gate to generate a Bell state. However, under such qubit allocation, the prepared \\(\left\vert 0/{1}_{L}\right\rangle\\) state is not fault-tolerant. Therefore, we adopt the qubit allocation scheme from the previous section to simultaneously fault-tolerantly prepare the \\(\left\vert 0/{1}_{L}\right\rangle\\) and \\(\left\vert {\pm }_{L}\right\rangle\\) states (see Fig. 3c). This circuit can be viewed as a special planarization of a two-layer architecture. In this layout, all physical CZ gates required in both the logical state preparation and the transversal CNOT gate implementation are 2-D hardware-neighbor. We reconstruct the density matrix of the logical Bell states in Fig. 3d. The overall fidelities in the preparation and characterization for the four logical Bell states are 79.5(5)%, 79.5(5)%, 79.4(5)%, and 79.4(5)%, respectively. We also report the post-selection rates for Bell states under _X_ ⊗ _X_ , _X_ ⊗ _X_ , _Z_ ⊗ _Z_ measurements along with a comparison between simulated and experimental data in Fig. 3e. Correspondingly, we prepare four physical Bell states by physical CNOT gate on qubits q6 and q7. The fidelity of the CNOT gate between q6 and q7 is the highest among all physical CNOT gates in the experiment. The fidelities for the four physical Bell states are 74.4(9)%, 74.2(9)%, 74.5(9)%, and 74.2(9)%, respectively, all of which are lower than the fidelity of the fault-tolerantly prepared logical Bell states.
To confirm entanglement between the two surface code logical qubits, we verify a variant of the CHSH inequality48."). For a two-qubit density matrix _ρ_ , define the matrix _T_ _ρ_ with elements \\({({T}_{\rho })}_{ij}={\rm{Tr}}(\rho {P}_{i}\otimes {P}_{j})\\), where _P_ _i_ ∈ {_X_ , _Y_ , _Z_}. A necessary and sufficient condition for violating the CHSH inequality is _u_ 1 +  _u_ 2 > 1, where _u_ 1 and _u_ 2 are the two largest eigenvalues of the matrix \\({T}_{\rho }^{T}{T}_{\rho }\\). In our experiment, the values of _u_ 1 +  _u_ 2 for the four logical Bell states are 1.55, 1.55, 1.54, and 1.54, respectively. This result confirms the presence of quantum entanglement between the two surface code logical qubits.
### Logical single-qubit rotation
Finally, we demonstrated logical single-qubit rotations around the _Z_ or _X_ axis based on gate teleportation circuit (Fig. 4a). More specifically, these rotation operations are
$${R}_{Z}(\theta )={e}^{-i\theta {Z}_{L}/2},\quad {R}_{X}(\theta )={e}^{-i\theta {X}_{L}/2},$$
(6) 
where _θ_ is the rotation angle. The gate teleportation circuit consists of three parts. First, preparing the ancilla states
$$\begin{array}{rcl}\left\vert {\theta }_{L}^{z}\right\rangle &=&\frac{1}{\sqrt{2}}(\left\vert {0}_{L}\right\rangle +{e}^{i\theta }\left\vert {1}_{L}\right\rangle ),\\\ \left\vert {\theta }_{L}^{x}\right\rangle &=&\cos \frac{\theta }{2}\left\vert {0}_{L}\right\rangle -i\sin \frac{\theta }{2}\left\vert {1}_{L}\right\rangle .\end{array}$$
(7) 
Then the logical CNOT gate is applied, and finally, ancilla state is measured in logical _Z_ or _X_ basis. The _R_ _Z_(_θ_) or _R_ _X_(_θ_) gate is successfully executed only when the logical _Z_ or _X_ measurement results in +1; otherwise, operation _R_ _Z_(2 _θ_) or _R_ _X_(2 _θ_) needs to be applied as a compensation. Here, we simply use the post-selection strategy, that is, only retaining the cases where the measurement result is +1. Note that the ancilla states can be viewed as the result of applying _R_ _Z_(_θ_) or _R_ _X_(_θ_) gates to \\(\left\vert {+}_{L}\right\rangle\\) or \\(\left\vert {0}_{L}\right\rangle\\), respectively, that is why we refer to this circuit as gate teleportation circuit.
**Fig. 4: Logical single-qubit rotations and characterization.**

**a** Gate teleportation circuits that implement single-qubit rotation operations on logical qubits. The ± sign of the rotation angle depends on the measurement results of the ancilla logical states. **b** , **c** Circuits for applying single-qubit rotations _R_ _Z_(_θ_) and _R_ _X_(_θ_) on the logical state \\(\left\vert {\psi }_{L}\right\rangle\\) based on gate teleportation, respectively. **d** , **e** Average values of Pauli operators and fidelity of the ancilla logical states \\(\left\vert {\theta }_{L}^{z}\right\rangle\\) and \\(\left\vert {\theta }_{L}^{x}\right\rangle\\) with rotation angles _θ_ ∈ (− _π_ , _π_], respectively. Scatter points and solid lines are used to distinguish experimental and simulated data. **f** , **g** Average values of Pauli operators and fidelity of the output states \\({R}_{Z}(\theta )\left\vert {+}_{L}\right\rangle\\) or \\({R}_{X}(\theta )\left\vert {0}_{L}\right\rangle\\) with rotation angles _θ_ ∈ (− _π_ , _π_], respectively.
Full size image
In the experiment, we first prepare the required ancilla logical states \\(\left\vert {\theta }_{L}^{z}\right\rangle\\) and \\(\left\vert {\theta }_{L}^{x}\right\rangle\\) with _θ_ ∈ (− _π_ , _π_] on a chain of the quantum processor (q1-q4). Then these input states are measured in _X_ _L_ , _Y_ _L_ or _Z_ _L_ basis to obtain the expectation values of the logical Pauli operators. Subsequently, we execute the circuits in Fig. 4b, c, demonstrating the single-qubit rotation gates around the _Z_ or _X_ axis on the state \\(\left\vert {\psi }_{L}\right\rangle =\left\vert {+}_{L}\right\rangle\\) or \\(\left\vert {0}_{L}\right\rangle\\), respectively. The expectation values of the logical Pauli operators for the input and output states are shown in Fig. 4d–g. Using the expectation values 〈 _X_ 〉, 〈 _Y_ 〉, 〈 _Z_ 〉, we reconstructed the density matrices, thereby calculating the fidelity of each state. The average fidelities of input states \\(\left\vert {\theta }_{L}^{z}\right\rangle\\) and \\(\left\vert {\theta }_{L}^{x}\right\rangle\\) are evaluated to be 89.0(3)%. Correspondingly, the average fidelities of the output states are 78.0(9)% and 75.0(9)%, respectively.
To characterize the fidelity of the single-qubit logical gates, it is required to construct the LPTMs of these gates. Here, we test the LPTMs of _R_ _Z_(_θ_) and _R_ _X_(_θ_) with _θ_ ∈ {0, _π_ /4, _π_ /2, _π_} as examples. The input states are encoded as the logical states from the set \\(\\{\left\vert {+}_{L}\right\rangle ,\left\vert {-}_{L}\right\rangle ,\left\vert {0}_{L}\right\rangle ,\left\vert {i}_{L}\right\rangle \\}\\), and the above logical gates are applied separately. We measure the expectation values of the Pauli operators for the input and output states and construct the LPTMs for these eight logical gates accordingly (see Supplementary Note 2). The fidelities \\({F}_{L}^{G}\\) of these eight logical gates are estimated to be 94.4(5)%, 90.0(7)%, 87.4(7)%, 93.9(5)%, 92.1(6)%, 90.7(7)%, 89.6(7)%, 92.4(6)%, respectively.
## Discussion
This work experimentally demonstrates a complete universal set of logical gates on distance-2 surface code in a superconducting processor. Particularly, logical Bell states that violates CHSH inequality have been fault-tolerantly prepared using the transversal CNOT gate. Based on the logical CNOT gate, the gate teleportation process is experimentally demonstrated to implement single-qubit rotation operations. These results reveal several significant aspects of FTQC based on the surface code in superconducting hardware.
The fidelity of logical operations are in the experiment is affected by a variety of factors. The dominant noise of our superconducting processor is the readout noise and two-qubit gate noise. Through numerical simulations, we found that the performance of logical circuits in our experiment is more sensitive to readout errors compared to gate errors. The Supplementary Note 4 presents the results of these numerical simulations and discusses the mechanisms underlying various types of noise as well as potential approaches for improvement. In addition, in the implementation of single-qubit rotation gates, the fidelity of the logical gates largely depends on the quality of the ancilla logical states in the gate teleportation circuit. In our experiment, the ancilla logical states are generated by non-fault-tolerant preparation circuits, resulting in a relatively high error rate. In a complete FTQC framework, high-fidelity ancilla logical states are typically obtained through state distillation27."),49."),50."),51."). A particularly challenging future task is to experimentally demonstrate these distillation protocols.
In our experiment, logical qubits are confined to a one-dimensional structure without measurement qubits. A natural extension is to incorporate the repeated stabilizer measurement process into our work. Achieving both the stabilizer measurement process and transversal CNOT gate typically requires a multi-layer structure or long-range entangling gates (see Supplementary Note 6). For superconducting platforms, this is regarded as a challenging long-term goal. However, we are also excited to see that they are increasingly gaining attention due to the requirements in FTQC52."),53."),54."). Meanwhile, some prototypes of these technologies have been demonstrated recently43."),44."),45."),46."),55."), indicating that they are not beyond reach.
In conclusion, our experiment enriches the possibilities for research in FTQC. First, from a near-term perspective, our work demonstrates the role of error detection codes or small-distance error-correction codes in the early FTQC era. Notably, the performance of some logical circuits in the experiment surpassed that of physical circuits. Numerical simulations further indicate that the pseudo-threshold of the experimental circuits can significantly exceed the fault-tolerant threshold (approximately 1%, see Supplementary Note 4). Second, on superconducting platforms with planar nearest-neighbor connectivity, lattice surgery is the mainstream method for logical operations56."),57."),58."). Demonstrating transversal CNOT gates supports a hybrid scheme combining them with lattice surgery, potentially reducing the significant overhead of FTQC41."),42."). We have elaborated on the feasibility and benefits of this architecture in the Supplementary Note 6. Achieving this requires extending the experimental qubit layout to a multi-layer structure, which remains a long-term goal for superconducting platforms.
## Methods
### Fault-tolerant logical state preparation
Here, we prove that the circuits in the first two parts of Fig. 2a and b are fault-tolerant, meaning that a single-qubit error occurring at any position in the circuit can be detected without leading to a logical error. To clarify this, we note that there are two types of errors to consider: those that remain localized in a single qubit and are thus detectable by the stabilizers, and those that might affect the final state of more than one qubit. We focus on the latter type of errors, ensuring that they do not spread to become logical errors. For ease of discussion, we combine the _H_ gates and CZ gates in the circuit into CNOT gates, focusing on the preparation of the \\(\left\vert {0}_{L}\right\rangle\\) and \\(\left\vert {+}_{L}\right\rangle\\) states, resulting in the circuit shown in Fig. 5. This simplification does not affect the fault-tolerance of the original circuits.
**Fig. 5: Equivalent logical state fault-tolerant preparation circuit.**

The circuits are simplified to a composition of CNOT and _H_ gates, with fault tolerance equivalent to the original circuits. The possible _X_ (blue) or _Z_ (yellow) errors that could propagate are shown. **a** Fault-tolerant (FT) preparation circuit for \\(\left\vert 0\right\rangle\\). **b** Fault-tolerant (FT) preparation circuit for \\(\left\vert +\right\rangle\\).
Full size image
For the \\(\left\vert {0}_{L}\right\rangle\\) state preparation circuit, we only need to consider the Pauli _X_ errors in the circuit, as any logical _Z_ _L_ error produced is trivial for the \\(\left\vert 0/{1}_{L}\right\rangle\\) state up to a global phase. We mark the locations of all possible single-qubit Pauli _X_ errors (shown as blue _X_ in Fig. 5a). The leftmost _X_ error affects qubits 1 through 4 as _X_ 1 _X_ 2 _X_ 3 _X_ 4, which is a stabilizer. The second and third _X_ errors affect qubits 2 and 3 as _X_ 2 _X_ 3 and qubits 1 and 4 as _X_ 1 _X_ 4, respectively. These errors anti-commute with the stabilizers _Z_ 1 _Z_ 2 and _Z_ 3 _Z_ 4, and thus they will be detected by the stabilizer measurements. This proves that no single-qubit Pauli _X_ error at any position in the circuit can spread to become a logical _X_ _L_ error.
Similarly, in the \\(\left\vert {+}_{L}\right\rangle\\) state preparation circuit, we consider the possible Pauli _Z_ errors. The two possible spreading Pauli _Z_ errors (yellow _Z_ in Fig. 5b) affect qubits 1 and 2 as _Z_ 1 _Z_ 2 and qubits 3 and 4 as _Z_ 3 _Z_ 4, which are the two stabilizers of this code. Since all these errors can be detected or lead to a stabilizer operator, we have demonstrated the fault-tolerance of these two encoding circuits.
### Logical Pauli transfer matrix (LPTM)
The Pauli transfer matrix (PTM) describes a quantum process on the components of the density matrix represented in the basis of Pauli operators6."),59."),60."),61."). For a _d_ -dimensional Hilbert space, a PTM \\({\mathcal{R}}\\) is a linear transformation matrix from the expectation values _p_ _i_ = 〈 _P_ _i_ 〉 of the Pauli operators _P_ _i_ in the input state to the expectation values \\({p}_{j}^{{\prime} }\\) in the output state:
$${p}_{j}^{{\prime} }=\sum _{i}{{\mathcal{R}}}_{ij}{p}_{i}.$$
(8) 
In our experiment, _P_ _i_ belongs to \\({\\{{I}_{L},{X}_{L},{Y}_{L},{Z}_{L}\\}}^{\otimes 2}\\) and {_I_ _L_ , _X_ _L_ , _Y_ _L_ , _Z_ _L_} for the cases _d_ = 4 and _d_ = 2, respectively. To construct the LPTMs of the logical quantum gates in the main text, we use input states from the complete set \\({\\{\left\vert {+}_{L}\right\rangle ,\left\vert {-}_{L}\right\rangle ,\left\vert {0}_{L}\right\rangle ,\left\vert {i}_{L}\right\rangle \\}}^{\otimes 2}\\) (for the logical CNOT gate) or \\(\\{\left\vert {+}_{L}\right\rangle ,\left\vert {-}_{L}\right\rangle ,\left\vert {0}_{L}\right\rangle ,\left\vert {i}_{L}\right\rangle \\}\\) (for the logical single-qubit gates). The density matrices of the input and output states are obtained through logical state tomography, and the expectation values _p_ _i_ and \\({p}_{j}^{{\prime} }\\) are then calculated. The inverse of the expectation value matrix yields the raw result \\({{\mathcal{R}}}^{{\rm{raw}}}\\). However, \\({{\mathcal{R}}}^{{\rm{raw}}}\\) may not satisfy the conditions of a physical channel, i.e., being completely positive and trace-preserving6."). Therefore, using the techniques in ref. 31."), \\({{\mathcal{R}}}^{{\rm{raw}}}\\) is transformed into the Choi state representation:
$${\rho }_{{\rm{choi}}}=\frac{1}{{d}^{2}}\sum _{ij}{{\mathcal{R}}}_{ij}^{{\rm{raw}}}{P}_{j}^{T}\otimes {P}_{i}.$$
(9) 
We then optimize _ρ_ under the following objective function and constraints:
$$\begin{array}{rcl}{\rm{minimize}}&&\sum\limits_{i,j}{\left\vert {\rm{Tr}}(\rho {P}_{j}^{T}\otimes {P}_{i})-{{\mathcal{R}}}_{ij}^{{\rm{raw}}}\right\vert }^{2},\\\ {\rm{subject}}\,{\rm{to}}&&\rho \ge 0,{\rm{Tr}}(\rho )=1,{{\rm{Tr}}}_{1}(\rho )=\frac{1}{2}{\mathbb{1}},\end{array}$$
(10) 
where \\({{\rm{Tr}}}_{1}\\) is the partial trace over the left half subsystem. Using the convex optimization package _cvxpy_ , we obtain the optimal result _ρ_ opt. The corresponding LPTM \\({\mathcal{R}}\\) is
$${{\mathcal{R}}}_{ij}={\rm{Tr}}({\rho }_{{\rm{opt}}}{P}_{j}^{T}\otimes {P}_{i})$$
(11) 
and the fidelity of the logical gate is
$${F}_{L}^{G}=\frac{{\rm{Tr}}({{\mathcal{R}}}^{\dagger }{{\mathcal{R}}}_{{\rm{ideal}}})+d}{{d}^{2}+d},$$
(12) 
where \\({{\mathcal{R}}}_{{\rm{ideal}}}\\) is the ideal LPTM of the logical gate?. In our experiment, we constructed the LPTMs for the logical CNOT gate and eight logical single-qubit gates. The specific details of these LPTMs can be found in the Supplementary Note 2.
### Quantum state tomography
Quantum state tomography62."),63."),64.") reconstructs the density matrix of an unknown quantum state by measuring some observables. In our experiment, we measure 4 _n_ − 1 Pauli operators of the logical qubits, where _n_ is the number of logical qubits. Assuming the expectation values of these Pauli operators are _p_ _i_ = 〈 _P_ _i_ 〉, where \\({P}_{i}\in {\\{{I}_{L},{X}_{L},{Y}_{L},{Z}_{L}\\}}^{\otimes n}/\\{{I}_{L}^{\otimes n}\\}\\), the density matrix is reconstructed as:
$${\rho }_{L,0}=\mathop{\sum }\limits_{i=0}^{{4}^{n}-1}\frac{{p}_{i}{P}_{i}}{{2}^{n}},$$
with _p_ 0 = 1 and \\({P}_{0}={I}_{L}^{\otimes n}\\). Such a density matrix _ρ_ _L_ ,0 may not satisfy the physicality characteristics of a quantum state. Therefore, we use maximum likelihood estimation65."),66.") to construct the logical density matrix _ρ_ _L_. Specifically, the objective function to minimize is
$$\sum _{i}| {\rm{Tr}}({\rho }_{L}{P}_{i})-{p}_{i}{| }^{2},$$
(13) 
subject to \\({\rm{Tr}}({\rho }_{L})=1\\), and _ρ_ _L_ ≥ 0. This process is implemented also using the convex optimization package _cvxpy_. Likewise, we also apply state tomography to physical states for constructing the density operators of states \\(\left\vert 0\right\rangle\\), \\(\left\vert 1\right\rangle\\), \\(\left\vert +\right\rangle\\), \\(\left\vert -\right\rangle\\) and four Bell states, which is done for comparison with the logical state density matrices. These results are shown in the Supplementary Note 2.
## Data availability
The data that support the findings of this study are available from the corresponding author upon reasonable request.
## References
  1. Shor, P. Algorithms for quantum computation: discrete logarithms and factoring. In _Proc. 35th Annual Symposium on Foundations of Computer Science_ , 124–134 (IEEE, 1994).
  2. Freedman, M. H., Kitaev, A. & Wang, Z. Simulation of topological field theories by quantum computers. _Commun. Math. Phys._ **227** , 587–603 (2002).
Article ADS MathSciNet  Google Scholar
  3. Biamonte, J. et al. Quantum machine learning. _Nature_. **549** , 195–202 (2017).
Article ADS  Google Scholar
  4. Preskill, J. Reliable quantum computers. _Proc. R. Soc. Lond. Ser. A Math. Phys. Eng. Sci._ **454** , 385–410 (1998).
Article ADS  Google Scholar
  5. Gottesman, D. _Stabilizer Codes and Quantum Error Correction_ (California Institute of Technology, 1997).
  6. Nielsen, M. A. & Chuang, I. L._Quantum Computation and Quantum Information_ (Cambridge University Press, 2010).
  7. Andersen, C. K. et al. Repeated quantum error detection in a surface code. _Nat. Phys._ **16** , 875–880 (2020).
Article  Google Scholar
  8. Google Quantum AI. Exponential suppression of bit or phase errors with cyclic error correction. _Nature_. **595** , 383–387 (2021).
Article  Google Scholar
  9. Krinner, S. et al. Realizing repeated quantum error correction in a distance-three surface code. _Nature_. **605** , 669–674 (2022).
Article ADS  Google Scholar
  10. Zhao, Y. et al. Realization of an error-correcting surface code with superconducting qubits. _Phys. Rev. Lett._ **129** , 030501 (2022).
Article ADS  Google Scholar
  11. Google Quantum AI. Suppressing quantum errors by scaling a surface code logical qubit. _Nature_. **614** , 676–681 (2023).
Article ADS  Google Scholar
  12. Hetényi, B, Wootton, J. R. Creating entangled logical qubits in the heavy-hex lattice with topological codes. _PRX Quantum_ **5** , 040334 (2024).
  13. Acharya, R. et al. Quantum error correction below the surface code threshold. _Nature_. **638** , 920–926 (2024).
  14. Lacroix, N. et al. Scaling and logic in the color code on a superconducting quantum processor. _Nature_. **645** , 614–619 (2025).
  15. Eickbusch, A. et al. Demonstrating dynamic surface codes <https://arxiv.org/abs/2412.14360> (2024).
  16. da Silva, M. et al. Demonstration of logical qubits and repeated error correction with better-than-physical error rates. _arXiv preprint arXiv:2404.02280_ <https://doi.org/10.48550/arXiv.2404.02280> (2024).
  17. Ryan-Anderson, C. et al. Realization of real-time fault-tolerant quantum error correction. _Phys. Rev. X_. **11** , 041058 (2021).
 Google Scholar
  18. Bluvstein, D. et al. Logical quantum processor based on reconfigurable atom arrays. _Nature_. **626** , 58–65 (2024).
Article ADS  Google Scholar
  19. Campagne-Ibarcq, P. et al. Quantum error correction of a qubit encoded in grid states of an oscillator. _Nature_. **584** , 368–372 (2020).
Article  Google Scholar
  20. Gertler, J. M. et al. Protecting a bosonic qubit with autonomous quantum error correction. _Nature_. **590** , 243–248 (2021).
Article ADS  Google Scholar
  21. Sivak, V. et al. Real-time quantum error correction beyond break-even. _Nature_. **616** , 50–55 (2023).
Article ADS  Google Scholar
  22. Ni, Z. et al. Beating the break-even point with a discrete-variable-encoded logical qubit. _Nature_. **616** , 56–60 (2023).
Article ADS  Google Scholar
  23. Cai, W. et al. Protecting entanglement between logical qubits via quantum error correction. _Nat. Phys._ 1–5 <https://doi.org/10.1038/s41567-024-02446-8> (2024).
  24. Eastin, B. & Knill, E. Restrictions on transversal encoded quantum gate sets. _Phys. Rev. Lett._ **102** , 110502 (2009).
Article ADS  Google Scholar
  25. Chen, X., Chung, H., Cross, A. W., Zeng, B. & Chuang, I. L. Subsystem stabilizer codes cannot have a universal set of transversal gates for even one encoded qudit. _Phys. Rev. A_ **78** , 012353 (2008).
Article ADS  Google Scholar
  26. Zeng, B., Cross, A. & Chuang, I. L. Transversality versus universality for additive quantum codes. _IEEE Trans. Inf. Theory_ **57** , 6272–6284 (2011).
Article ADS MathSciNet  Google Scholar
  27. Bravyi, S. & Kitaev, A. Universal quantum computation with ideal clifford gates and noisy ancillas. _Phys. Rev. A_. **71** , 022316 (2005).
Article ADS MathSciNet  Google Scholar
  28. Fowler, A. G., Mariantoni, M., Martinis, J. M. & Cleland, A. N. Surface codes: Towards practical large-scale quantum computation. _Phys. Rev. A_. **86** , 032324 (2012).
Article ADS  Google Scholar
  29. Hu, L. et al. Quantum error correction and universal gate set operation on a binomial bosonic logical qubit. _Nat. Phys._ **15** , 503–508 (2019).
Article  Google Scholar
  30. Postler, L. et al. Demonstration of fault-tolerant universal quantum gate operations. _Nature_ **605** , 675–680 (2022).
Article ADS  Google Scholar
  31. Marques, J. F. et al. Logical-qubit operations in an error-detecting surface code. _Nat. Phys._ **18** , 80–86 (2022).
Article  Google Scholar
  32. Ryan-Anderson, C. et al. Implementing fault-tolerant entangling gates on the five-qubit code and the color code. _arXiv preprint arXiv:2208.01863_ <https://doi.org/10.48550/arXiv.2208.01863> (2022).
  33. Abobeih, M. H. et al. Fault-tolerant operation of a logical qubit in a diamond quantum processor. _Nature_ **606** , 884–889 (2022).
Article ADS  Google Scholar
  34. Menendez, D. H., Ray, A. & Vasmer, M. Implementing fault-tolerant non-clifford gates using the [8, 3, 2] color code. _Phys Rev A_ **109** , 062438 (2024).
  35. Shaw, M. H., Doherty, A. C. & Grimsmo, A. L. Logical gates and read-out of superconducting gottesman-kitaev-preskill qubits. _arXiv preprint arXiv:2403.02396_ <https://doi.org/10.1038/s41567-018-0414-3> (2024).
  36. Yifei, W. et al. Efficient fault-tolerant implementations of non-clifford gates with reconfigurable atom arrays. _Npj Quantum Inf._ **10** , 136 (2024).
  37. Besedin, I. et al. Realizing lattice surgery on two distance-three repetition codes with superconducting qubits <https://arxiv.org/abs/2501.04612> (2025).
  38. Wang, D. S., Fowler, A. G. & Hollenberg, L. C. L. Surface code quantum computing with error rates over 1%. _Phys. Rev. A_ **83** , 020302 (2011).
Article ADS  Google Scholar
  39. Self, C. N., Benedetti, M. & Amaro, D. Protecting expressive circuits with a quantum error detection code. _Nat. Phys._ **20** , 219–224 (2024).
Article  Google Scholar
  40. Ginsberg, T. & Patel, V. Quantum error detection for early term fault-tolerant quantum algorithms <https://arxiv.org/abs/2503.10790> (2025).
  41. Cai, Z., Siegel, A. & Benjamin, S. Looped pipelines enabling effective 3d qubit lattices in a strictly 2d device. _PRX Quantum_ **4** , 020345 (2023).
Article ADS  Google Scholar
  42. Viszlai, J., Lin, S. F., Dangwal, S., Baker, J. M. & Chong, F. T. An architecture for improved surface code connectivity in neutral atoms. _arXiv preprint arXiv:2309.13507_ <https://doi.org/10.48550/arXiv.2309.13507> (2023).
  43. Rosenberg, D. et al. 3d integrated superconducting qubits. _npj Quantum Inf._ **3** , 42 (2017).
Article ADS  Google Scholar
  44. Yost, D.-R. W. et al. Solid-state qubits integrated with superconducting through-silicon vias. _npj Quantum Inf._ **6** , 59 (2020).
Article ADS  Google Scholar
  45. Rosenberg, D. et al. Solid-state qubits: 3d integration and packaging. _IEEE Microw. Mag._ **21** , 72–85 (2020).
Article  Google Scholar
  46. Gold, A. et al. Entanglement across separate silicon dies in a modular superconducting qubit device. _npj Quantum Inf._ **7** , 142 (2021).
Article ADS  Google Scholar
  47. Nation, P. D., Kang, H., Sundaresan, N. & Gambetta, J. M. Scalable mitigation of measurement errors on quantum computers. _PRX Quantum_. **2** , 040326 (2021).
Article ADS  Google Scholar
  48. Horodecki, R., Horodecki, P. & Horodecki, M. Violating bell inequality by mixed spin-12 states: necessary and sufficient condition. _Phys. Lett. A_. **200** , 340–344 (1995).
Article ADS MathSciNet  Google Scholar
  49. Bravyi, S. & Haah, J. Magic-state distillation with low overhead. _Phys. Rev. A_. **86** , 052329 (2012).
Article ADS  Google Scholar
  50. Litinski, D. Magic State Distillation: Not as Costly as You Think. _Quantum_. **3** , 205 (2019).
Article  Google Scholar
  51. Campbell, E. T. & O’Gorman, J. An efficient magic state approach to small angle rotations. _Quantum Sci. Technol._ **1** , 015007 (2016).
Article ADS  Google Scholar
  52. Bravyi, S., Dial, O., Gambetta, J. M., Gil, D. & Nazario, Z. The future of quantum computing with superconducting qubits. _J. Appl. Phys._ **132** , 160902 (2022).
Article ADS  Google Scholar
  53. Bravyi, S. et al. High-threshold and low-overhead fault-tolerant quantum memory. _Nature_. **627** , 778–782 (2024).
Article ADS  Google Scholar
  54. Ramette, J., Sinclair, J., Breuckmann, N. P. & Vuletić, V. Fault-tolerant connection of error-corrected qubits with noisy links. _npj Quantum Inf._ **10** , 58 (2024).
Article ADS  Google Scholar
  55. Wang, K. et al. Demonstration of low-overhead quantum error correction codes <https://arxiv.org/abs/2505.09684> (2025).
  56. Horsman, D., Fowler, A. G., Devitt, S. & Meter, R. V. Surface code quantum computing by lattice surgery. _N. J. Phys._ **14** , 123011 (2012).
Article MathSciNet  Google Scholar
  57. Litinski, D. & Oppen, F. V. Lattice surgery with a twist: simplifying Clifford gates of surface codes. _Quantum_. **2** , 62 (2018).
Article  Google Scholar
  58. Litinski, D. A game of surface codes: large-scale quantum computing with lattice surgery. _Quantum_. **3** , 128 (2019).
Article  Google Scholar
  59. Chow, J. M. et al. Universal quantum gate set approaching fault-tolerant thresholds with superconducting qubits. _Phys. Rev. Lett._ **109** , 060501 (2012).
Article ADS  Google Scholar
  60. Greenbaum, D. Introduction to quantum gate set tomography. _arXiv preprint arXiv:1509.02921_ <https://doi.org/10.48550/arXiv.1509.02921> (2015).
  61. Nielsen, E. et al. Gate set tomography. _Quantum_. **5** , 557 (2021).
Article  Google Scholar
  62. Raymer, M. G., Beck, M. & McAlister, D. Complex wave-field reconstruction using phase-space tomography. _Phys. Rev. Lett._ **72** , 1137–1140 (1994).
Article ADS MathSciNet  Google Scholar
  63. Leonhardt, U. Quantum-state tomography and discrete Wigner function. _Phys. Rev. Lett._ **74** , 4101–4105 (1995).
Article ADS MathSciNet  Google Scholar
  64. Cramer, M. et al. Efficient quantum state tomography. _Nat. Commun._ **1** , 149 (2010).
Article ADS  Google Scholar
  65. Banaszek, K., D’Ariano, G. M., Paris, M. G. A. & Sacchi, M. F. Maximum-likelihood estimation of the density matrix. _Phys. Rev. A_. **61** , 010304 (1999).
Article ADS  Google Scholar
  66. Smolin, J. A., Gambetta, J. M. & Smith, G. Efficient method for computing the maximum-likelihood quantum state from measurements with additive Gaussian noise. _Phys. Rev. Lett._ **108** , 070502 (2012).
Article ADS  Google Scholar

Download references
## Acknowledgements
We thank Prof. Chang-Ling Zou and Prof. Ying Li for reviewing the manuscript and providing valuable suggestions, and thank Cheng Xue and Xi-Ning Zhuang for their assistance reviewing this manuscript. This work is supported by the National Key Research and Development Program of China (Grant No. 2024YFB4504101 and Grant No. 2023YFB4502500).
## Author information
### Authors and Affiliations
  1. Laboratory of Quantum Information, University of Science and Technology of China, Hefei, Anhui, PR China
Jiaxuan Zhang, Bin-Han Lu, Hai-Feng Zhang, Jia-Ning Li, Peng Duan, Yu-Chun Wu & Guo-Ping Guo
  2. Anhui Province Key Laboratory of Quantum Network, Hefei, Anhui, PR China
Jiaxuan Zhang, Bin-Han Lu, Hai-Feng Zhang, Jia-Ning Li, Peng Duan, Yu-Chun Wu & Guo-Ping Guo
  3. Institute of Artificial Intelligence, Hefei Comprehensive National Science Center, Hefei, Anhui, PR China
Jiaxuan Zhang, Zhao-Yun Chen, Yu-Chun Wu & Guo-Ping Guo
  4. Institute of the Advanced Technology, University of Science and Technology of China, Hefei, Anhui, PR China
Yun-Jie Wang
  5. Origin Quantum Computing, Hefei, Anhui, PR China
Guo-Ping Guo

Authors
  1. Jiaxuan Zhang
View author publications
Search author on:PubMedGoogle Scholar
  2. Zhao-Yun Chen
View author publications
Search author on:PubMedGoogle Scholar
  3. Yun-Jie Wang
View author publications
Search author on:PubMedGoogle Scholar
  4. Bin-Han Lu
View author publications
Search author on:PubMedGoogle Scholar
  5. Hai-Feng Zhang
View author publications
Search author on:PubMedGoogle Scholar
  6. Jia-Ning Li
View author publications
Search author on:PubMedGoogle Scholar
  7. Peng Duan
View author publications
Search author on:PubMedGoogle Scholar
  8. Yu-Chun Wu
View author publications
Search author on:PubMedGoogle Scholar
  9. Guo-Ping Guo
View author publications
Search author on:PubMedGoogle Scholar

### Contributions
Jiaxuan Zhang conceived and designed the project. Peng Duan, Yu-Chun Wu, and Guo-Ping Guo provide overall supervision throughout the research. Jiaxuan Zhang, Zhao-Yun Chen, Bin-Han Lu and Hai-Feng Zhang performed the majority of the experiments. Jiaxuan Zhang and Yun-Jie Wang contributed to data analysis. Jiaxuan Zhang drafted the initial manuscript. All authors discussed the results and reviewed the final version of the manuscript.
### Corresponding authors
Correspondence to Peng Duan, Yu-Chun Wu or Guo-Ping Guo.
## Ethics declarations
### Competing interests
The authors declare no competing interests.
## Additional information
**Publisher’s note** Springer Nature remains neutral with regard to jurisdictional claims in published maps and institutional affiliations.
## Supplementary information
### Supplementary Information
## Rights and permissions
**Open Access** This article is licensed under a Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License, which permits any non-commercial use, sharing, distribution and reproduction in any medium or format, as long as you give appropriate credit to the original author(s) and the source, provide a link to the Creative Commons licence, and indicate if you modified the licensed material. You do not have permission under this licence to share adapted material derived from this article or parts of it. The images or other third party material in this article are included in the article’s Creative Commons licence, unless indicated otherwise in a credit line to the material. If material is not included in the article’s Creative Commons licence and your intended use is not permitted by statutory regulation or exceeds the permitted use, you will need to obtain permission directly from the copyright holder. To view a copy of this licence, visit <http://creativecommons.org/licenses/by-nc-nd/4.0/>.
Reprints and permissions
## About this article

### Cite this article
Zhang, J., Chen, ZY., Wang, YJ. _et al._ Demonstrating a universal logical gate set in error-detecting surface codes on a superconducting quantum processor. _npj Quantum Inf_ **11** , 177 (2025). https://doi.org/10.1038/s41534-025-01118-6
Download citation
  * Received: 13 February 2025
  * Accepted: 22 September 2025
  * Published: 14 November 2025
  * Version of record: 14 November 2025
  * DOI: https://doi.org/10.1038/s41534-025-01118-6

### Share this article
Anyone you share the following link with will be able to read this content:
Get shareable link
Sorry, a shareable link is not currently available for this article.
Copy shareable link to clipboard
Provided by the Springer Nature SharedIt content-sharing initiative 
### Subjects
  * Quantum information
  * Qubits

Close banner Close
!Nature Briefing
Sign up for the _Nature Briefing_ newsletter — what matters in science, free to your inbox daily.
Close banner Close
Get the most important science stories of the day, free in your inbox. Sign up for Nature Briefing 
  *[DOI]: Digital Object Identifier
