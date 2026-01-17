---
title: "Quantum error correction of qudits beyond break-even | Nature"
source: https://www.nature.com/articles/s41586-025-08899-y
date: 2025-05-14
description: "Hilbert space dimension&nbsp;is a key resource for quantum information processing1,2. Not only is a large overall Hilbert space an essential requirement for quantum error correction, but a large local"
word_count: 8168
---

## Your privacy, your choice
We use essential cookies to make sure the site can function. We also use optional cookies for advertising, personalisation of content, usage analysis, and social media, as well as to allow video information to be shared for both marketing, analytics and editorial purposes.
By accepting optional cookies, you consent to the processing of your personal data - including transfers to third parties. Some third parties are outside of the European Economic Area, with varying standards of data protection.
See our privacy policy for more information on the use of your personal data.
Manage preferences for further information and to change your choices.
Accept all cookies Reject optional cookies
Skip to main content
Thank you for visiting nature.com. You are using a browser version with limited support for CSS. To obtain the best experience, we recommend you use a more up to date browser (or turn off compatibility mode in Internet Explorer). In the meantime, to ensure continued support, we are displaying the site without styles and JavaScript.
Quantum error correction of qudits beyond break-even 
 Download PDF 
 Download PDF 
## Abstract
Hilbert space dimension is a key resource for quantum information processing1."),2."). Not only is a large overall Hilbert space an essential requirement for quantum error correction, but a large local Hilbert space can also be advantageous for realizing gates and algorithms more efficiently3."),4."),5."),6."),7."). As a result, there has been considerable experimental effort in recent years to develop quantum computing platforms using qudits (_d_ -dimensional quantum systems with _d_ > 2) as the fundamental unit of quantum information8."),9."),10."),11."),12."),13."),14."),15."),16."),17."),18."),19."). Just as with qubits, quantum error correction of these qudits will be necessary in the long run, but so far, error correction of logical qudits has not been demonstrated experimentally. Here we report the experimental realization of an error-corrected logical qutrit (_d_ = 3) and ququart (_d_ = 4), which was achieved with the Gottesman–Kitaev–Preskill bosonic code20."). Using a reinforcement learning agent21."),22."), we optimized the Gottesman–Kitaev–Preskill qutrit (ququart) as a ternary (quaternary) quantum memory and achieved beyond break-even error correction with a gain of 1.82 ± 0.03 (1.87 ± 0.03). This work represents a novel way of leveraging the large Hilbert space of a harmonic oscillator to realize hardware-efficient quantum error correction.
### Similar content being viewed by others

###  Extending the computational reach of a superconducting qutrit processor 
Article Open access 14 October 2024

###  Noisy qudit vs multiple qubits: conditions on gate efficiency for enhancing fidelity 
Article Open access 10 June 2024

###  A universal qudit quantum processor with trapped ions 
Article 21 July 2022
## Main
The number of quantum states available to a quantum computer, quantified by its Hilbert space dimension, is a fundamental and precious resource1."),2."). Crucially, the goal of achieving quantum advantage at scale relies on the ability to manipulate an exponentially large Hilbert space with subexponentially many operations. This large Hilbert space is typically realized using _N_ qubits (two-level quantum systems), giving rise to a 2 _N_ -dimensional Hilbert space. However, most physical realizations of qubits have many more than two available states. These valuable extra quantum states often go untapped, because the methods for working with qudits (_d_ -level quantum systems with _d_ > 2) as the fundamental unit of quantum information are more complicated and less well developed than those for working with qubits23.").
On the other hand, embracing these qudits could enable more efficient distillation of magic states24."),25."), synthesis of gates3."),4."), compilation of algorithms5."),6."),7."), and simulation of high-dimensional quantum systems26."),27."). For these reasons, considerable experimental effort has been spent in recent years on developing qudit-based platforms for quantum computing, using donor spins in silicon8."), ultracold atoms and molecules9."),10."), optical photons11."),12."), superconducting circuits13."),14."),15."), trapped ions16."),17."), and vacancy centers18."),19."). If qudits are to be useful in the long run, however, quantum error correction (QEC) will be necessary.
In this work we experimentally demonstrate QEC of logical qudits with _d_ > 2, using the Gottesman–Kitaev–Preskill (GKP) bosonic code20.") to realize a logical qutrit (_d_ = 3) and ququart (_d_ = 4) encoded in grid states of an oscillator. Our optimized GKP qutrit (ququart) lived longer, on average, than the best physical qutrit (ququart) available in our system by a factor of 1.82 ± 0.03 (1.87 ± 0.03), making this one of only a handful of experiments to beat the break-even point of QEC for quantum memories22."),28."),29."),30."). This experiment represents a novel way of leveraging the large Hilbert space of an oscillator and builds on previous realizations of GKP qubits22."),31."),32."),33."),34."),35."),36.") and bosonic codes28."),29."),37."). Access to a higher-dimensional error-corrected manifold of quantum states may enable more hardware-efficient architectures for quantum information processing.
## Error correction of GKP qudits
Our experimental device is the same as in ref. 22.") and consists of a tantalum transmon38."),39.") dispersively coupled to a three-dimensional superconducting microwave cavity40."), as shown in Fig. 1a. The cavity hosts an oscillator mode (described by Fock states \\(\\{| n\rangle :n\in {{\mathbb{Z}}}_{\ge 0}\\}\\) and mode operator _a_), which is used for storing our logical GKP states. The transmon hosts a qubit (described by ground and excited states {|_g_ ⟩, |_e_ ⟩} and Pauli operators _σ_ _x_ ,_y_ ,_z_), which is used as an ancilla for controlling the oscillator and performing error correction. The cavity has an energy relaxation lifetime of _T_ 1,c = 631 μs and Ramsey coherence time _T_ 2R,c = 1,030 μs, whereas the transmon has lifetime _T_ 1,q = 295 μs and Hahn-echo lifetime _T_ 2E,q = 286 μs (Supplementary Information section I).
**Fig. 1: Stabilizing GKP qudits.**

**a** , Schematic of the experimental device. **b** , Geometric structure of the displacement operators that define the single-mode square GKP code. **c** , Circuit for one round of finite-energy GKP qudit stabilization, generalizing the SBS protocol43."). The big ECD gate48.") of amplitude \\({{\ell }}_{d,\varDelta }=\sqrt{{\rm{\pi }}d}\cosh ({\varDelta }^{2})\\) is approximately the stabilizer length. The small ECD gates of amplitude \\({\varepsilon }_{d}/2=\sqrt{{\rm{\pi }}d}\sinh ({\varDelta }^{2})/2\\) account for the envelope size _Δ_. At the end of SBS round _j_ , the cavity phase is updated by _ϕ_ _j_ (Methods). **d** , Measured real part of the characteristic function of the maximally mixed GKP qudit state for _d_ = 1 to 4 with _Δ_ = 0.3, prepared by performing 300 SBS rounds starting from the cavity in its vacuum state |0⟩.
Full size image
We employed the single-mode square GKP code20."), which is designed to be translationally symmetric in phase space. The structure of the code comes from the geometric phase associated with displacement operators _D_(_α_) = exp(_αa_ † −  _α_ *_a_) in phase space, as depicted in Fig. 1b. Two displacements commute up to a phase given by twice the area _A_ they enclose, such that _D_(_α_ 1)_D_(_α_ 2) = exp(2i _A_)_D_(_α_ 2)_D_(_α_ 1) with \\(A=\text{Im}[{\alpha }_{1}{\alpha }_{2}^{* }]\\). The ideal code has stabilizer generators _S_ _X_ =  _D_(_ℓ_ _d_) and _S_ _Z_ =  _D_(_i_ _ℓ_ _d_), where _ℓ_ _d_ is the stabilizer length. If these stabilizers are to have a common +1 eigenspace (the code space), they must commute, which means they must enclose an area π _d_ in phase space for positive integer _d_ , such that \\({{\ell }}_{d}=\sqrt{{\rm{\pi }}d}\\), where _d_ is the dimension of the code space. The code words of this idealized logical qudit are grids of position eigenstates |_q_ ⟩, with form
$${| {Z}_{n}\rangle }_{d}\propto \mathop{\sum }\limits_{k=-\infty }^{\infty }| q=n\sqrt{2{\rm{\pi }}/d}+k\sqrt{2{\rm{\pi }}d}\rangle ,$$
(1) 
where _n_ = 0, 1, …,  _d_ − 1 and \\(q=(a+{a}^{\dagger })/\sqrt{2}\\) is the position operator. Note that with our choice of phase-space units, translations in position and displacements along the real axis of phase space differ in amplitude by a factor of \\(\sqrt{2}\\). The logical operators of the ideal code are the displacement operators \\({X}_{d}=D(\sqrt{{\rm{\pi }}/d})\\) and \\({Z}_{d}=D({\rm{i}}\sqrt{{\rm{\pi }}/d})\\), which act on the code space as
$$\begin{array}{l}{Z}_{d}{| {Z}_{n}\rangle }_{d}={({\omega }_{d})}^{n}{| {Z}_{n}\rangle }_{d},\\\ {X}_{d}{| {Z}_{n}\rangle }_{d}={| {Z}_{(n+1)\text{mod}d}\rangle }_{d},\end{array}$$
(2) 
where _ω_ _d_ = exp(2πi/_d_) is the primitive _d_ th root of unity. These operators _Z_ _d_ and _X_ _d_ are the generalized Pauli operators41."),42."), which are unitary but no longer Hermitian for _d_ > 2. These operators obey the generalized commutation relation _Z_ _d_ _X_ _d_ =  _ω_ _d_ _X_ _d_ _Z_ _d_ , determined by the area these displacements enclose in phase space. Compared to GKP qubits, GKP qudits have a longer stabilizer length that is proportional to \\(\sqrt{d}\\), such that they encode information further out in phase space, and a shorter distance between logical states that is proportional to \\(1/\sqrt{d}\\).
In practice, we work with an approximate finite-energy version of this code, which is obtained by applying the Gaussian envelope operator _E_ _Δ_ = exp(− _Δ_ 2 _a_ † _a_) to both the operators and states of the ideal code43."),44."). The parameter _Δ_ determines both the squeezing of individual quadrature peaks in the grid states as well as their overall extent in energy. For smaller _Δ_ , the peaks are more highly squeezed and the states have more energy. On increasing _d_ , we expect to require smaller _Δ_ , as the logical states are more closely spaced and contain information further out in phase space (at higher energies). With smaller _Δ_ , we expect the lifetime of our GKP qudits to decrease, as having more energy amplifies the rate of oscillator photon loss, and having information stored further out in phase space amplifies the effects of oscillator dephasing.
To stabilize the finite-energy GKP qudit manifold, we adapted the small-big-small (SBS) protocol43.") to the stabilizer length \\({{\ell }}_{d}=\sqrt{{\rm{\pi }}d}\\), as shown in Fig. 1c. This circuit, consisting of echoed conditional displacement (ECD) gates \\({\rm{ECD}}(\beta )=D(\beta /2)| e\rangle \langle g| +D(-\beta /2)| g\rangle \langle e| \\) and ancilla qubit rotations _R_ _φ_(_θ_) = exp[i(_σ_ _x_ cos  _φ_ +  _σ_ _y_ sin  _φ_)_θ_ /2], realizes an engineered dissipation onto the finite-energy GKP qudit manifold that removes the entropy associated with physical errors in the oscillator before they can accumulate into logical errors (Supplementary Information section II-A)32."),43."). In these expressions,  _β_ is the complex amplitude of the conditional displacement,  _φ_ is the azimuthal angle defining the rotation axis and  _θ_ is the rotation angle. This protocol is autonomous, requiring only a reset of the ancilla between rounds. We update the reference phase of the cavity mode between rounds to stabilize both quadratures in phase space (Methods).
To verify that this generalized SBS protocol works, we ran it for 300 rounds, starting with the cavity in vacuum, which prepared the maximally mixed state of the finite-energy GKP qudit \\({\rho }_{d}^{{\rm{mix}}}\,=\\)\\((1/d){\sum }_{n=0}^{d-1}| {Z}_{n}\rangle \langle {Z}_{n}{| }_{d}\\). We performed characteristic function tomography45.") of \\({\rho }_{d}^{{\rm{mix}}}\\) prepared in this way, the results of which are shown in Fig. 1d. As expected from its definition \\({\mathcal{C}}(\beta )=\langle D(\beta )\rangle \\), the characteristic function of these states has peaks at the stabilizer lengths, which increase with _d_ according to \\({{\ell }}_{d}=\sqrt{{\rm{\pi }}d}\\). The negative regions of \\(\text{Re}[{\mathcal{C}}(\beta )]\\) for odd _d_ are a consequence of the geometric phase associated with displacement operators \\(D({e}^{{\rm{i}}{\rm{\pi }}/4}\sqrt{2{\rm{\pi }}d})={(-1)}^{d}D(\sqrt{{\rm{\pi }}d})D({\rm{i}}\sqrt{{\rm{\pi }}d})\\). However, it is interesting to note that the states \\({\rho }_{d}^{{\rm{mix}}}\\) for odd _d_ have regions of Wigner negativity (Supplementary Information section II-B) and are, therefore, non-classical46.").
## Characterizing quantum memories
To characterize the performance of our logical qudits as quantum memories and establish the concept of QEC gain for qudits, we followed previous work22.") and used the average channel fidelity \\({{\mathcal{F}}}_{d}({\mathcal{E}},I)\\), which quantifies how well a channel \\({\mathcal{E}}\\) realizes the identity _I_ (ref. 47.")). Although \\({{\mathcal{F}}}_{d}\\) will have a non-exponential time evolution in general, it can always be expanded to short times d _t_ as \\({{\mathcal{F}}}_{d}({\mathcal{E}},I)\approx 1-((d-1)/d)\varGamma {\rm{d}}t\\), where _Γ_ is the effective decay rate of the channel \\({\mathcal{E}}\\) at short times. This rate _Γ_ enables us to compare different decay channels on the same footing. In particular, we want to compare the decay rate \\({\varGamma }_{d}^{{\rm{logical}}}\\) of our logical qudit to \\({\varGamma }_{d}^{{\rm{physical}}}\\) of the best physical qudit in our system. We define the QEC gain as their ratio \\({G}_{d}={\varGamma }_{d}^{{\rm{physical}}}/{\varGamma }_{d}^{{\rm{logical}}}\\), and the break-even point is when this gain is unity.
The average channel fidelity can be expressed in terms of the probabilities \\({\langle \psi | {\mathcal{E}}(| \psi \rangle \langle \psi | )| \psi \rangle }_{d}\\) that our error-correction channel \\({\mathcal{E}}\\) preserves the qudit state |_ψ_ ⟩ _d_ , summed over a representative set of states {|_ψ_ ⟩ _d_} (Supplementary Information section III). Each of these probabilities entails a separate experiment in which we prepare the state |_ψ_ ⟩ _d_ , perform error correction, and measure our logical qudit in a basis containing |_ψ_ ⟩ _d_. Herein lies the primary experimental challenge of the present work: devising ways of measuring our logical GKP qudit in bases containing each state in our representative set {|_ψ_ ⟩ _d_} using only binary measurements of our ancilla qubit.
For the qutrit in _d_ = 3, our representative set of states are the bases {|_P_ _n_ ⟩3: _n_ = 0, 1, 2} of Pauli operators \\(P\in {{\mathcal{P}}}_{3}=\\{{X}_{3},{Z}_{3},{X}_{3}{Z}_{3},{X}_{3}^{2}{Z}_{3}\\}\\), defined by _P_ |_P_ _n_ ⟩3 =  _ω_ _n_ |_P_ _n_ ⟩3 for _ω_ = exp(2πi/3). The effective decay rate of our logical GKP qutrit can then be expressed as
$${\varGamma }_{3}^{{\rm{GKP}}}=\frac{1}{12}\sum _{P\in {{\mathcal{P}}}_{3}}\mathop{\sum }\limits_{n=0}^{2}{\gamma }_{{P}_{n}},$$
(3) 
where \\({\gamma }_{{P}_{n}}\\) is the rate at which the state |_P_ _n_ ⟩3 decays to \\({\rho }_{3}^{{\rm{mix}}}\\). For the ququart in _d_ = 4, our representative set of states consists of two types of bases. The first type are the bases {|_P_ _n_ ⟩4: _n_ = 0, 1, 2, 3} of Pauli operators \\(P\in {{\mathcal{P}}}_{4}=\\{{X}_{4},{Z}_{4},\sqrt{\omega }{X}_{4}{Z}_{4},{X}_{4}^{2}{Z}_{4},\sqrt{\omega }{X}_{4}^{3}{Z}_{4},{X}_{4}{Z}_{4}^{2}\\}\\), defined by _P_ |_P_ _n_ ⟩4 =  _ω_ _n_ |_P_ _n_ ⟩4 for _ω_ = i. The second type is what we call the ququart parity basis {|±, _m_ ⟩4: _m_ = 0, 1} consisting of the simultaneous eigenstates of \\({X}_{4}^{2}\\) and \\({Z}_{4}^{2}\\), such that \\({X}_{4}^{2}{| \pm ,m\rangle }_{4}=\pm {| \pm ,m\rangle }_{4}\\) and \\({Z}_{4}^{2}{| \pm ,m\rangle }_{4}={(-1)}^{m}{| \pm ,m\rangle }_{4}\\). The effective decay rate of our logical GKP ququart can then be expressed as
$${\varGamma }_{4}^{{\rm{GKP}}}=\frac{1}{20}\left[\sum _{P\in {{\mathcal{P}}}_{4}}\mathop{\sum }\limits_{n=0}^{3}{\gamma }_{{P}_{n}}-\sum _{\begin{array}{c}s=\pm \\\ m=0,1\end{array}}{\gamma }_{\pm ,m}\right],$$
(4) 
where \\({\gamma }_{{P}_{n}}\\) (_γ_ ±,_m_) is the rate at which the Pauli eigenstate |_P_ _n_ ⟩4 (parity state |±, _m_ ⟩4) decays to \\({\rho }_{4}^{{\rm{mix}}}\\).
As a basis of comparison, the best physical qudit in our system is the cavity Fock qudit spanned by the states |0⟩, |1⟩, …, |_d_ − 1⟩. The cavity hosting this qudit decoheres under both photon loss and pure dephasing at rates _κ_ 1,c = 1/_T_ 1,c and _κ_ ϕ,c = 1/_T_ 2R,c − 1/2 _T_ 1,c. From these measured rates, we can extrapolate the effective decay rate \\({\varGamma }_{d}^{{\rm{Fock}}}\\) of the cavity Fock qudit under these decoherence channels. For _d_ = 2 through 4, we obtained \\({\varGamma }_{2}^{{\rm{Fock}}}={(851\pm 9\mu {\rm{s}})}^{-1}\\), \\({\varGamma }_{3}^{{\rm{Fock}}}={(488\pm 7\mu {\rm{s}})}^{-1}\\) and \\({\varGamma }_{4}^{{\rm{Fock}}}={(332\pm 6\mu {\rm{s}})}^{-1}\\).
## Logical qutrit beyond break-even
To measure the effective decay rate of the logical GKP qutrit through equation (3), we needed to prepare all of the eigenstates of the Pauli operators in \\({{\mathcal{P}}}_{3}\\) and perform measurements in the basis of these Pauli operators. To prepare the eigenstates |_P_ _n_ ⟩3, we used interleaved sequences of ECD gates and transmon rotations, which enable universal control of the oscillator mode in the cavity48."). We optimized depth-8 ECD circuits to implement the unitary that maps the cavity vacuum state |0⟩ to the desired state |_P_ _n_ ⟩3 with envelope size _Δ_ = 0.32. The measured Wigner functions \\(W(\alpha )=\langle D(\alpha ){e}^{i\pi {a}^{\dagger }a}D(\,-\alpha )\rangle \\) of our prepared |_P_ 0⟩3 states are shown in Fig. 2a (see Supplementary Information section V-A for the other eigenstates). In general, the eigenstates |_P_ _n_ ⟩ _d_ are oriented in phase space in the direction of the displacement induced by _P_ _d_ , where _X_ _d_ displaces rightward, _Z_ _d_ displaces upward, and \\({P}_{d}^{d-1}={P}_{d}^{-1}\\).
**Fig. 2: Realization of a logical GKP qutrit.**

**a** , State preparation of qutrit Pauli eigenstates |_P_ 0⟩3 with _Δ_ = 0.32. **b** , Circuit for measuring a qutrit in the basis of Pauli operator _P_ 3 using an ancilla qubit, where \\({\theta }_{0}=2\,\arctan (1/\sqrt{2})\\). The first measurement distinguishes between the state |_P_ 0⟩3 and the subspace {|_P_ 1⟩3, |_P_ 2⟩3}, whereas the second distinguishes between |_P_ 1⟩3 and {|_P_ 0⟩3, |_P_ 2⟩3}. The Bloch spheres depict the trajectories taken by the ancilla when the qutrit is in each Pauli eigenstate. **c** , Backaction of the qutrit Pauli measurement in the _Z_ 3 basis, applied to the maximally mixed qutrit state. **d** , Decay of qutrit Pauli eigenstates |_P_ 0⟩3 under the optimized QEC protocol. The dashed black lines indicate a probability of 1/3. The solid grey lines are exponential fits. From left to right, we found \\({\gamma }_{{X}_{0}}^{-1}=\mathrm{1,153}\pm 13\,\mu {\rm{s}}\\), \\({\gamma }_{{Z}_{0}}^{-1}=\mathrm{1,120}\pm 15\,\mu {\rm{s}}\\), \\({\gamma }_{X{Z}_{0}}^{-1}=743\pm 10\,\mu {\rm{s}}\\) and \\({\gamma }_{{X}^{2}{Z}_{0}}^{-1}=727\pm 11\,\mu {\rm{s}}\\).
Full size image
We measured the GKP qutrit in Pauli basis _P_ 3 using the circuit shown in Fig. 2b. The generalized ancilla-qubit-controlled Pauli operators _CP_ 3 = |_g_ ⟩⟨ _g_ |_I_ 3 + |_e_ ⟩⟨ _e_ |_P_ 3 were realized with ECD gates, where _I_ _d_ is the identity operator in dimension _d_. Intuitively, as generalized Pauli operators on GKP qudits are implemented through displacements, the conditional versions of these displacements implement _C_ _P_ _d_ operations, with some technical caveats (Methods). The idea of this circuit is to perform a projective measurement in the _P_ 3 basis using two binary measurements of the ancilla qubit. The first measurement determines whether the qutrit is in state |_P_ 0⟩3 or the {|_P_ 1⟩3, |_P_ 2⟩3} subspace, and the second determines whether the qutrit is in state |_P_ 1⟩3 or the {|_P_ 0⟩3, |_P_ 2⟩3} subspace. These two binary measurements uniquely determine the ternary measurement result in the _P_ 3 basis and collapse the qutrit state accordingly. Note that this circuit was constructed for the ideal code and incurs infidelity when applied to the finite-energy code. To verify that this circuit realizes the desired projective measurement, we prepared \\({\rho }_{3}^{{\rm{mix}}}\\), measured in the _Z_ 3 basis, and performed Wigner tomography of the cavity post-selected on the three measurement outcomes. The results of this measurement are shown in Fig. 2c (see Supplementary Information section V-B for the other Pauli bases).
With these techniques, we used a reinforcement learning agent21.") to optimize the logical GKP qutrit as a ternary quantum memory following the method in ref. 22.") (Methods). We then evaluated the optimal QEC protocol by preparing each eigenstate |_P_ _n_ ⟩3 for each \\({P}_{3}\in {{\mathcal{P}}}_{3}\\), implementing the optimized QEC protocol for a variable number of rounds, and measuring the final state in the _P_ 3 basis. Finally, we fitted an exponential decay to each probability \\(\langle {P}_{n}| {\mathcal{E}}(| {P}_{n}\rangle \langle {P}_{n}| )| {P}_{n}\rangle \\) to obtain \\({\gamma }_{{P}_{n}}\\). The results of this evaluation for the |_P_ 0⟩3 states are shown in Fig. 2d (the other results are given in Supplementary Information section V-C). As with the GKP qubit22."),33."), we found longer lifetimes for the ‘Cartesian’ eigenstates of _X_ 3 and _Z_ 3 than for the remaining ‘diagonal’ eigenstates, as the latter were more susceptible to both cavity photon-loss errors and ancilla bit-flip errors33."). Using equation (3) with our measured rates \\({\gamma }_{{P}_{n}}\\), we obtained \\({\varGamma }_{3}^{{\rm{GKP}}}={(886\pm 3\mu {\rm{s}})}^{-1}\\). Comparing with \\({\varGamma }_{3}^{{\rm{Fock}}}\\), we obtained the QEC gain
$${G}_{3}={\varGamma }_{3}^{{\rm{Fock}}}/{\varGamma }_{3}^{{\rm{GKP}}}=1.82\pm 0.03,$$
(5) 
which is well beyond the break-even point.
## Logical ququart beyond break-even
We followed a similar procedure to measure the effective decay rate of our logical GKP ququart through equation (4) as we did for the qutrit, the main difference being that we needed to prepare and measure states in both the ququart parity basis and the Pauli bases \\(P\in {{\mathcal{P}}}_{4}\\). We again used depth-8 ECD circuits48.") to prepare the Pauli eigenstates |_P_ _n_ ⟩4 and parity states |±, _m_ ⟩4 with _Δ_ = 0.32. The measured Wigner functions of our prepared |_P_ 0⟩4 states and the |+, 0⟩4 state are shown in Fig. 3a (see Supplementary Information section VI-A for the remaining states). Again, the eigenstates |_P_ _n_ ⟩ _d_ are oriented in phase space in the direction of the displacement induced by _P_ _d_. By contrast, the parity states |±, _m_ ⟩ are uniform grids, equally oriented both horizontally and vertically.
**Fig. 3: Realization of a logical GKP ququart.**

**a** , State preparation of ququart Pauli eigenstates |_P_ 0⟩4 and parity state |+, 0⟩4 with _Δ_ = 0.32. **b** , Circuit for measuring a ququart in the basis of Pauli operator _P_ 4 using an ancilla qubit. The first measurement distinguishes between the even and odd states |_P_ even/odd⟩4, and the second measurement distinguishes between the remaining two states. The Bloch spheres depict the trajectories taken by the ancilla when the ququart is in each Pauli eigenstate. **c** , Backaction of the GKP ququart Pauli measurement in the _Z_ 4 basis applied to the maximally mixed ququart state. **d** , Circuit for measuring a ququart in the parity basis {|±, _m_ ⟩4: _m_ = 0, 1}, where \\({X}_{4}^{2}{| \pm ,m\rangle }_{4}=\pm {| \pm ,m\rangle }_{4}\\) and \\({Z}_{4}^{2}{| \pm ,m\rangle }_{4}={(-1)}^{m}{| \pm ,m\rangle }_{4}\\). The first measurement determines the eigenvalue of \\({X}_{4}^{2}\\), and the second determines that of \\({Z}_{4}^{2}\\). **e** , Backaction of the GKP ququart parity measurement applied to the maximally mixed ququart state. **f** , Decay of ququart Pauli eigenstates |_P_ 0⟩4 and parity state |+, 0⟩4 under the optimized QEC protocol. The dashed black lines indicate a probability of 1/4. The solid grey lines are exponential fits. From left to right, we found \\({\gamma }_{{X}_{0}}^{-1}=840\pm 8\,{\rm{\mu }}{\rm{s}}\\), \\({\gamma }_{{Z}_{0}}^{-1}=836\pm 9\,\mu {\rm{s}}\\), \\({\gamma }_{X{Z}_{0}}^{-1}=519\pm 6\,\mu {\rm{s}}\\), \\({\gamma }_{{X}^{2}{Z}_{0}}^{-1}\,=507\pm 9\,\mu {\rm{s}}\\), \\({\gamma }_{{X}^{3}{Z}_{0}}^{-1}=571\pm 7\,\mu {\rm{s}}\\), \\({\gamma }_{X{Z}_{0}^{2}}^{-1}=562\pm 9\,\mu {\rm{s}}\\) and \\({\gamma }_{+,0}^{-1}=607\pm 8\,\mu {\rm{s}}\\).
Full size image
We measured the GKP ququart in Pauli basis _P_ 4 using the circuit shown in Fig. 3b. The first binary measurement of the ancilla qubit distinguishes between the even subspace {|_P_ 0⟩, |_P_ 2⟩} and odd subspace {|_P_ 1⟩, |_P_ 3⟩} by measuring whether \\({P}_{4}^{2}=\pm 1\\), and the second distinguishes between the remaining two states by measuring _P_ 4 = ±1 (if in the even subspace) or _P_ 4 = ±i (if in the odd subspace). To verify that this circuit realizes the desired projective measurement, we prepared \\({\rho }_{4}^{{\rm{mix}}}\\), measured in the _Z_ 4 basis, and performed Wigner tomography of the cavity post-selected on the four measurement outcomes. The results of this measurement are shown in Fig. 3c (see Supplementary Information section VI-B for the other Pauli bases).
We measured the GKP ququart in the parity basis {|±, _m_ ⟩4: _m_ = 0, 1} using the circuit shown in Fig. 3d. The first binary measurement of the ancilla qubit determines whether \\({X}_{4}^{2}=\pm 1\\), and the second determines whether \\({Z}_{4}^{2}=\pm 1\\). To verify that this circuit realizes the desired projective measurement, we prepared \\({\rho }_{4}^{{\rm{mix}}}\\), measured in the parity basis, and performed Wigner tomography post-selected on the four measurement outcomes. The results of this measurement are shown in Fig. 3e. As with the qutrit, our logical ququart measurements were constructed for the ideal code, and they incur infidelity when applied to the finite-energy code.
With these techniques, we again used a reinforcement learning agent21.") to optimize the logical GKP ququart as a quaternary quantum memory following the method in ref. 22.") (Methods). We then evaluated the optimal QEC protocol by preparing each eigenstate |_P_ _n_ ⟩4 for each \\({P}_{4}\in {{\mathcal{P}}}_{4}\\) (plus the parity basis), implementing the optimized QEC protocol for a variable number of rounds, and measuring the final state in its corresponding basis. Finally, we fitted an exponential decay to each probability \\(\langle {P}_{n}| {\mathcal{E}}(| {P}_{n}\rangle \langle {P}_{n}| )| {P}_{n}\rangle \\) and \\(\langle \pm ,m| \,{\mathcal{E}}(| \pm ,m\rangle \langle \pm ,m| )| \pm ,m\rangle \\) to obtain \\({\gamma }_{{P}_{n}}\\) and _γ_ ±,_m_ , respectively. The results of this evaluation for the |_P_ 0⟩4 states and |+, 0⟩4 state are shown in Fig. 3f (the remaining results are given in Supplementary Information section VI-C). Again, we found longer lifetimes for the Cartesian eigenstates of _X_ 4 and _Z_ 4 than for the remaining eigenstates. Using equation (4) with our measured rates \\({\gamma }_{{P}_{n}}\\) and _γ_ ±,_m_ , we obtained \\({\varGamma }_{4}^{{\rm{GKP}}}={(620\pm 2\mu {\rm{s}})}^{-1}\\). Comparing with \\({\varGamma }_{4}^{{\rm{Fock}}}\\), we obtained the QEC gain
$${G}_{4}={\varGamma }_{4}^{{\rm{Fock}}}/{\varGamma }_{4}^{{\rm{GKP}}}=1.87\pm 0.03,$$
(6) 
which is, again, well beyond the break-even point.
## Discussion
Notably, despite the increasing complexity of the code, we found that the QEC gain stayed roughly constant at about 1.8 as we increased the dimension of our logical GKP qudit from 2 to 4, as shown in Fig. 4a. Note that the gain _G_ 2 = 1.81 ± 0.02 that we achieved with the GKP qubit is less than the gain of 2.3 previously reported using the same device22."), which was due to changes in both the device and the experimental conditions (see Supplementary Information section IV-C for details). Regardless, the measurements shown in Fig. 4a were taken under the same conditions, and they indicate that as we increased _d_ from 2 to 4, the lifetime of our logical GKP qudit decreased at about the same rate as that of our cavity Fock qudit.
**Fig. 4: Comparing GKP qudits.**

**a** , Effective lifetime of the physical cavity Fock qudit and logical GKP qudit for _d_ ∈ {2, 3, 4}. The arrows indicate the QEC gain. **b** , Effective envelope size _Δ_ eff of the optimized GKP qudit for _d_ ∈ {2, 3, 4}. **c** , Mean number of photons in the cavity for the optimized GKP qudit for _d_ ∈ {2, 3, 4}.
Full size image
This decrease occurred because the GKP qudit states are more closely spaced and contain information further out in phase space for increasing _d_ , which should require smaller _Δ_. To verify this, we prepared \\({\rho }_{d}^{{\rm{mix}}}\\) of our optimal GKP qudits and measured the central Gaussian peak of their characteristic functions for _d_ = 2 through 4 (Supplementary Information). The width _Δ_ eff of this Gaussian is related to the parameter _Δ_ and decreased with _d_ , as shown in Fig. 4b, in agreement with our expectations. The average number of photons ⟨ _a_ † _a_ ⟩ in \\({\rho }_{d}^{{\rm{mix}}}\\) can also be inferred from this measurement of _Δ_ eff and is presented in Fig. 4c.
With a smaller _Δ_ , our logical GKP qudits had more energy and were more highly squeezed, which should amplify the rates of cavity photon loss and dephasing. To corroborate this, we simulated our optimal QEC protocols and isolated the relative contributions of different physical errors to our overall logical error rates (Supplementary Information section VII). We found that the three largest sources of logical errors were transmon bit flips, whose relative contribution decreased as _d_ was increased, cavity photon loss, whose relative contribution increased as _d_ was increased, and cavity dephasing, which was the dominant source of error and whose relative contribution increased as _d_ was increased. As our cavity dephasing was primarily due to the thermal population _n_ th = 2.2 ± 0.1% of the transmon40."), the lifetimes of our logical GKP qudits could be substantially improved by either reducing _n_ th or using an ancilla that can be actively decoupled from the cavity when not in use49."),50.").
In summary, we have demonstrated QEC of logical qudits with _d_ > 2, which represents a milestone achievement in the development of qudits for useful quantum technologies. Moreover, we have beaten the break-even point for QEC of quantum memories, a result few other experiments have accomplished22."),28."),29."),30."). These results rely upon many technical advances, such as our generalization of previous experimental methods22.") and our invention of protocols for measuring qudits in generalized Pauli bases. Our work builds on the promise of hardware efficiency offered by bosonic codes22."),28."),29."),31."),32."),33."),34."),35."),36."),37.") and represents a novel way of leveraging the large Hilbert space of an oscillator. In exchange for a modest reduction in lifetime, we gained access to more logical quantum states in a single physical system. This could enable more efficient compilation of gates3."),4.") and algorithms5."),6."),7."), alternative techniques for quantum communication51.") and transduction52."), and advantageous strategies for concatenation into an external multi-qudit code24."),25."). Such a concatenation requires entangling gates, which for GKP qudits can be realized with the same operations used for entangling GKP qubits20."),53."),54."),55."). With the realization of bosonic logical qudits, we have also established a platform for concatenating codes internally. By embedding a logical qubit within a bosonic logical qudit20."),56."),57."),58."), multiple layers of error correction can be implemented inside a single oscillator.
## Methods
### Phase update between stabilization rounds
The generalized SBS circuit in Fig. 1c realizes autonomous QEC of the finite-energy GKP code with respect to the ideal stabilizer \\({S}_{X}=D(\sqrt{{\rm{\pi }}d})\\) (ref. 43.")). The analogous circuit for \\({S}_{Z}=D({\rm{i}}\sqrt{{\rm{\pi }}d})\\) is obtained by updating the phase of all subsequent cavity operations by π/2, which transforms _q_ →  _p_ and _p_ → − _q_ in the rotating frame of the cavity, where \\(q=(a+{a}^{\dagger })/\sqrt{2}\\) is the position of the cavity and \\(p={\rm{i}}({a}^{\dagger }-a)/\sqrt{2}\\) is the momentum. To mitigate the effects of experimental imperfections, we symmetrized the protocol by also performing QEC with respect to stabilizers \\({S}_{X}^{\dagger }\\) and \\({S}_{Z}^{\dagger }\\), which are related to the circuit in Fig. 1c by phase updates of π and 3π/2, respectively. The full protocol is periodic with respect to four SBS rounds, as each stabilizer (\\({S}_{X},{S}_{Z},{S}_{X}^{\dagger },{S}_{Z}^{\dagger }\\)) is measured once per period.
Ideally, we would measure the stabilizer _S_ _X_ by implementing the ancilla-controlled-stabilizer operation \\(C{X}_{d}^{d}\\), but in practice, we instead used \\({\rm{ECD}}(\sqrt{{\rm{\pi }}d})=D(-\sqrt{{\rm{\pi }}d}/2)C{X}_{d}^{d}{\sigma }_{x}\\) (refs. 33."),48.")). For even dimensions _d_ , the extra displacement \\(D(-\sqrt{{\rm{\pi }}d}/2)\\) is the ideal Pauli operator \\({X}_{d}^{d/2}\\), the effect of which can be tracked in software (a similar result holds for the other stabilizers). In this case, we are free to measure the stabilizers in any order. We chose to increment the cavity phase in each round according to
$${\phi }_{j}^{(d\,{\rm{even}})}={\rm{\pi }}/2,$$
(7) 
which measures the stabilizers in the order _S_ _X_ , _S_ _Z_ , \\({S}_{X}^{\dagger }\\) and \\({S}_{Z}^{\dagger }\\). However, for odd _d_ , the displacement \\(D(-\sqrt{{\rm{\pi }}d}/2)\\) takes us outside the code space, an effect we have to reverse before moving on to measure _S_ _Z_. To do so, we incremented the cavity phase in each round according to
$${\phi }_{j}^{(d\,{\rm{o}}{\rm{d}}{\rm{d}})}=\left\\{\begin{array}{cc}{\rm{\pi }}, & j\equiv 0\,({\rm{m}}{\rm{o}}{\rm{d}}\,4),\\\ -{\rm{\pi }}/2, & j\equiv 1\,({\rm{m}}{\rm{o}}{\rm{d}}\,4),\\\ {\rm{\pi }}, & j\equiv 2\,({\rm{m}}{\rm{o}}{\rm{d}}\,4),\\\ {\rm{\pi }}/2, & j\equiv 3\,({\rm{m}}{\rm{o}}{\rm{d}}\,4),\end{array}\right.$$
(8) 
which measures the stabilizers in the order _S_ _X_ , \\({S}_{X}^{\dagger }\\), _S_ _Z_ and \\({S}_{Z}^{\dagger }\\).
### Compiling generalized controlled Pauli gates
The ancilla-controlled version of generalized Pauli operator \\({P}_{d}={e}^{{\rm{i}}\varphi }{X}_{d}^{n}{Z}_{d}^{m}\\) on the ideal GKP code is given by _CP_ _d_ = |_g_ ⟩⟨ _g_ | + |_e_ ⟩⟨ _e_ | ei _ϕ_ _D_(_β_ _n_)_D_(_β_ _m_), where \\({\beta }_{n}=n\sqrt{{\rm{\pi }}/d}\\) and \\({\beta }_{m}={\rm{i}}m\sqrt{{\rm{\pi }}/d}\\). We compiled _C_ _P_ _d_ in terms of ancilla rotations and a single ECD gate:
$${\rm{ECD}}({\beta }_{nm})=D(-{\beta }_{nm}/2)(| g\rangle \langle e| +| e\rangle \langle g| D({\beta }_{nm})),$$
(9) 
where _β_ _n_ _m_ =  _β_ _n_ +  _β_ _m_. Using _D_(_β_ _nm_) = exp(i _nm_ π/_d_)_D_(_β_ _n_)_D_(_β_ _m_), this can be rewritten as
$${\rm{ECD}}({\beta }_{nm})=D(-{\beta }_{nm}/2){\sigma }_{z}({\varphi }_{nm})C{P}_{d}{\sigma }_{x},$$
(10) 
where _φ_ _n_ _m_ =  _n_ _m_ π/_d_ −  _φ_ and _σ_ _z_(_θ_) = |_g_ ⟩⟨ _g_ | + |_e_ ⟩⟨ _e_ | ei _θ_. Rearranging terms, we obtain
$$C{P}_{d}=D({\beta }_{nm}/2){\sigma }_{z}(-{\varphi }_{nm}){\rm{ECD}}({\beta }_{nm}){\sigma }_{x}.$$
(11) 
In our experiments, we omitted the unconditional displacement _D_(_β_ _n_ _m_ /2) when compiling _C_ _P_ _d_ gates, which affected the backaction of our GKP qudit logical measurements (Figs. 2 and 3). In addition, we used the smallest amplitude ∣ _β_ _n_ _m_ ∣ consistent with the Pauli operator _P_ _d_. As an example, for _n_ =  _d_ − 1 and _d_ > 2, we used \\({\beta }_{nm}=-\sqrt{{\rm{\pi }}/d}+{\rm{i}}m\sqrt{{\rm{\pi }}/d}\\) because \\({X}_{d}^{d-1}={X}_{d}^{-1}\\). We emphasize that this _C_ _P_ _d_ gate is designed for the ideal GKP code and will necessarily incur infidelity when applied to the finite-energy code, but it may be possible to adapt this construction to the finite-energy case32."),43."),55.").
### Optimizing the QEC protocol
To optimize our generalized SBS protocol (Fig. 1c), we followed the method described in ref. 22."), parametrizing the SBS circuit using 45 free parameters in total. Anticipating that the larger conditional displacements required for GKP qudit stabilization (nominally proportional to \\(\sqrt{{\rm{\pi }}d}\\)) would take longer to execute, we fixed the duration of each SBS round to be 7 μs (instead of 5 μs as in ref. 22.")).
We used a reinforcement learning agent to optimize our QEC protocol over these 45 parameters in a model-free way21."). In each training epoch, the agent sends a batch of ten parametrizations **p** _i_ to the experiment, collects a reward _R_ _i_ for each, and updates its policy to increase the reward. For our reward, we measured the probability that the QEC protocol keeps our logical qudit in its initial state, operationally quantified by
$${R}_{i}=\frac{1}{2}[{\langle {Z}_{0}| {{\mathcal{E}}}_{{{\bf{p}}}_{i}}^{N}(| {Z}_{0}\rangle \langle {Z}_{0}| )| {Z}_{0}\rangle }_{d}+{\langle {X}_{1}| {{\mathcal{E}}}_{{{\bf{p}}}_{i}}^{N}(| {X}_{1}\rangle \langle {X}_{1}| )| {X}_{1}\rangle }_{d}],$$
(12) 
where \\({{\mathcal{E}}}_{{{\bf{p}}}_{i}}^{N}\\) is the channel corresponding to _N_ rounds of the SBS protocol parametrized by **p** _i_. For our optimal GKP qubit, we used _N_ = 140 and 200 training epochs, for the qutrit, _N_ = 80 and 200 training epochs, and for the ququart, _N_ = 80 and 300 training epochs.
Regarding the scalability of this optimization method, we emphasize that the resources required for training our GKP qudits are the same as for the GKP qubit and that this training was implemented using off-the-shelf consumer electronics. Because the training was performed for individual qubits or qudits, we expect that it could be parallelized across an array of such systems, yielding a resource requirement that scales only linearly with the system size. We expect that applying reinforcement learning to optimize entangling gates will be more complicated than applying it to individual qubits or qudits.
## Data availability
The data that support the findings of this study are available at Zenodo (<https://doi.org/10.5281/zenodo.15009817>)59.").
## Code availability
The code used for reinforcement learning is available at GitHub (<https://github.com/bbrock89/quantum_control_rl_server>).
## References
  1. Blume-Kohout, R., Caves, C. M. & Deutsch, I. H. Climbing mount scalable: physical resource requirements for a scalable quantum computer. _Found. Phys._ **32** , 1641–1670 (2002).
Article MathSciNet  Google Scholar
  2. Greentree, A. D. et al. Maximizing the Hilbert space for a finite number of distinguishable quantum states. _Phys. Rev. Lett._ **92** , 097901 (2004).
Article ADS PubMed  Google Scholar
  3. Ralph, T. C., Resch, K. J. & Gilchrist, A. Efficient Toffoli gates using qudits. _Phys. Rev. A_ **75** , 022313 (2007).
Article ADS  Google Scholar
  4. Fedorov, A., Steffen, L., Baur, M., Da Silva, M. P. & Wallraff, A. Implementation of a Toffoli gate with superconducting circuits. _Nature_ **481** , 170–172 (2012).
Article ADS CAS  Google Scholar
  5. Bocharov, A., Roetteler, M. & Svore, K. M. Factoring with qutrits: Shor’s algorithm on ternary and metaplectic quantum architectures. _Phys. Rev. A_ **96** , 012306 (2017).
Article ADS  Google Scholar
  6. Gokhale, P. et al. Asymptotic improvements to quantum circuits via qutrits. In _Proc. 46th International Symposium on Computer Architecture_ <https://doi.org/10.1145/3307650.3322253> (ACM, 2019).
  7. Chu, J. et al. Scalable algorithm simplification using quantum AND logic. _Nat. Phys._ **19** , 126–131 (2023).
Article CAS  Google Scholar
  8. Fernández De Fuentes, I. et al. Navigating the 16-dimensional Hilbert space of a high-spin donor qudit with electric and magnetic fields. _Nat. Commun._ **15** , 1380 (2024).
Article ADS PubMed PubMed Central  Google Scholar
  9. Vilas, N. B. et al. An optical tweezer array of ultracold polyatomic molecules. _Nature_ **628** , 282–286 (2024).
Article ADS CAS PubMed  Google Scholar
  10. Chaudhury, S. et al. Quantum control of the hyperfine spin of a Cs atom ensemble. _Phys. Rev. Lett._ **99** , 163002 (2007).
Article ADS PubMed  Google Scholar
  11. Kues, M. et al. On-chip generation of high-dimensional entangled quantum states and their coherent control. _Nature_ **546** , 622–626 (2017).
Article ADS CAS PubMed  Google Scholar
  12. Chi, Y. et al. A programmable qudit-based quantum processor. _Nat. Commun._ **13** , 1166 (2022).
Article ADS CAS PubMed PubMed Central  Google Scholar
  13. Nguyen, L. B. et al. Empowering a qudit-based quantum processor by traversing the dual bosonic ladder. _Nat. Commun._ **15** , 7117 (2024).
Article CAS PubMed PubMed Central  Google Scholar
  14. Roy, S. et al. Synthetic high angular momentum spin dynamics in a microwave oscillator. _Phys. Rev. X_ **15** , 021009 (2025).
  15. Wang, Z., Parker, R. W., Champion, E. & Blok, M. S. High-_E_ _J_ /_E_ _C_ transmon qudits with up to 12 levels. _Phys. Rev. Appl._ **23** , 034046 (2025).
  16. Leupold, F. M. et al. Sustained state-independent quantum contextual correlations from a single ion. _Phys. Rev. Lett._ **120** , 180401 (2018).
Article ADS CAS PubMed  Google Scholar
  17. Ringbauer, M. et al. A universal qudit quantum processor with trapped ions. _Nat. Phys._ **18** , 1053–1057 (2022).
Article CAS  Google Scholar
  18. Adambukulam, C., Johnson, B., Morello, A. & Laucht, A. Hyperfine spectroscopy and fast, all-optical arbitrary state initialization and readout of a single, ten-level 73Ge vacancy nuclear spin qudit in diamond. _Phys. Rev. Lett._ **132** , 060603 (2024).
Article ADS CAS PubMed  Google Scholar
  19. Soltamov, V. A. et al. Excitation and coherent control of spin qudit modes in silicon carbide at room temperature. _Nat. Commun._ **10** , 1678 (2019).
Article ADS CAS PubMed PubMed Central  Google Scholar
  20. Gottesman, D., Kitaev, A. & Preskill, J. Encoding a qubit in an oscillator. _Phys. Rev. A_ **64** , 012310 (2001).
Article ADS  Google Scholar
  21. Sivak, V. et al. Model-free quantum control with reinforcement learning. _Phys. Rev. X_ **12** , 011059 (2022).
CAS  Google Scholar
  22. Sivak, V. V. et al. Real-time quantum error correction beyond break-even. _Nature_ **616** , 50–55 (2023).
Article ADS CAS PubMed  Google Scholar
  23. Wang, Y., Hu, Z., Sanders, B. C. & Kais, S. Qudits and high-dimensional quantum computing. _Front. Phys._ **8** , 589504 (2020).
Article  Google Scholar
  24. Campbell, E. T., Anwar, H. & Browne, D. E. Magic-state distillation in all prime dimensions using quantum Reed-Muller codes. _Phys. Rev. X_ **2** , 041021 (2012).
CAS  Google Scholar
  25. Campbell, E. T. Enhanced fault-tolerant quantum computing in _d_ -level systems. _Phys. Rev. Lett._ **113** , 230501 (2014).
Article ADS CAS PubMed  Google Scholar
  26. Meth, M. et al. Simulating two-dimensional lattice gauge theories on a qudit quantum computer. _Nat. Phys._ **21** , 570–576 (2025).
  27. Sawaya, N. P. D. et al. Resource-efficient digital quantum simulation of _d_ -level systems for photonic, vibrational, and spin-s Hamiltonians. _npj Quantum Inf._ **6** , 49 (2020).
Article ADS  Google Scholar
  28. Ofek, N. et al. Extending the lifetime of a quantum bit with error correction in superconducting circuits. _Nature_ **536** , 441–445 (2016).
Article ADS CAS PubMed  Google Scholar
  29. Ni, Z. et al. Beating the break-even point with a discrete-variable-encoded logical qubit. _Nature_ **616** , 56–60 (2023).
Article ADS CAS PubMed PubMed Central  Google Scholar
  30. Google Quantum AI and Collaborators. Quantum error correction below the surface code threshold. _Nature_ **638** , 920–926 (2025).
  31. Flühmann, C. et al. Encoding a qubit in a trapped-ion mechanical oscillator. _Nature_ **566** , 513–517 (2019).
Article ADS PubMed  Google Scholar
  32. De Neeve, B., Nguyen, T.-L., Behrle, T. & Home, J. P. Error correction of a logical grid state qubit by dissipative pumping. _Nat. Phys._ **18** , 296–300 (2022).
Article  Google Scholar
  33. Campagne-Ibarcq, P. et al. Quantum error correction of a qubit encoded in grid states of an oscillator. _Nature_ **584** , 368–372 (2020).
Article CAS PubMed  Google Scholar
  34. Lachance-Quirion, D. et al. Autonomous quantum error correction of Gottesman–Kitaev–Preskill states. _Phys. Rev. Lett._ **132** , 150607 (2024).
Article ADS CAS PubMed  Google Scholar
  35. Konno, S. et al. Logical states for fault-tolerant quantum computation with propagating light. _Science_ **383** , 289–293 (2024).
Article ADS MathSciNet CAS PubMed  Google Scholar
  36. Matsos, V. G. et al. Universal quantum gate set for Gottesman–Kitaev–Preskill logical qubits. Preprint at <https://arxiv.org/abs/2409.05455> (2024).
  37. Gertler, J. M. et al. Protecting a bosonic qubit with autonomous quantum error correction. _Nature_ **590** , 243–248 (2021).
Article ADS CAS PubMed  Google Scholar
  38. Place, A. P. M. et al. New material platform for superconducting transmon qubits with coherence times exceeding 0.3 milliseconds. _Nat. Commun._ **12** , 1779 (2021).
Article ADS CAS PubMed PubMed Central  Google Scholar
  39. Ganjam, S. et al. Surpassing millisecond coherence in on chip superconducting quantum memories by optimizing materials and circuit design. _Nat. Commun._ **15** , 3687 (2024).
Article ADS CAS PubMed PubMed Central  Google Scholar
  40. Reagor, M. et al. Quantum memory with millisecond coherence in circuit QED. _Phys. Rev. B_ **94** , 014506 (2016).
Article ADS  Google Scholar
  41. Weyl, H. _The Theory of Groups and Quantum Mechanics_ (Dover Publications, 1950).
  42. Schwinger, J. Unitary operator bases. _Proc. Natl Acad. Sci. USA_ **46** , 570 (1960).
Article ADS MathSciNet CAS PubMed PubMed Central  Google Scholar
  43. Royer, B., Singh, S. & Girvin, S. Stabilization of finite-energy Gottesman-Kitaev-Preskill states. _Phys. Rev. Lett._ **125** , 260509 (2020).
Article ADS CAS PubMed  Google Scholar
  44. Grimsmo, A. L. & Puri, S. Quantum error correction with the Gottesman-Kitaev-Preskill code. _PRX Quantum_ **2** , 020101 (2021).
Article ADS  Google Scholar
  45. Flühmann, C. & Home, J. Direct characteristic-function tomography of quantum states of the trapped-ion motional oscillator. _Phys. Rev. Lett._ **125** , 043602 (2020).
Article ADS PubMed  Google Scholar
  46. Kenfack, A. & Życzkowski, K. Negativity of the Wigner function as an indicator of non-classicality. _J. Opt. B_ **6** , 396 (2004).
Article ADS MathSciNet  Google Scholar
  47. Nielsen, M. A. A simple formula for the average gate fidelity of a quantum dynamical operation. _Phys. Lett. A_ **303** , 249–252 (2002).
Article ADS MathSciNet CAS  Google Scholar
  48. Eickbusch, A. et al. Fast universal control of an oscillator with weak dispersive coupling to a qubit. _Nat. Phys._ **18** , 1464–1469 (2022).
Article CAS  Google Scholar
  49. Rosenblum, S. et al. Fault-tolerant detection of a quantum error. _Science_ **361** , 266–270 (2018).
Article ADS MathSciNet CAS PubMed  Google Scholar
  50. Ding, A. Z. et al. Quantum control of an oscillator with a Kerr-cat qubit. Preprint at <https://arxiv.org/abs/2407.10940> (2024).
  51. Schmidt, F., Miller, D. & Van Loock, P. Error-corrected quantum repeaters with Gottesman–Kitaev–Preskill qudits. _Phys. Rev. A_ **109** , 042427 (2024).
Article ADS MathSciNet CAS  Google Scholar
  52. Wang, Z. & Jiang, L. Passive environment-assisted quantum communication with GKP states. _Phys. Rev. X_ **15** , 021003 (2025).
  53. Terhal, B. M., Conrad, J. & Vuillot, C. Towards scalable bosonic quantum error correction. _Quantum Sci. Technol._ **5** , 043001 (2020).
Article ADS  Google Scholar
  54. Schmidt, F. & van Loock, P. Quantum error correction with higher Gottesman–Kitaev–Preskill codes: minimal measurements and linear optics. _Phys. Rev. A_ **105** , 042427 (2022).
Article ADS MathSciNet CAS  Google Scholar
  55. Rojkov, I. et al. Two-qubit operations for finite-energy Gottesman–Kitaev–Preskill encodings. _Phys. Rev. Lett._ **133** , 100601 (2024).
Article PubMed  Google Scholar
  56. Cafaro, C., Maiolini, F. & Mancini, S. Quantum stabilizer codes embedding qubits into qudits. _Phys. Rev. A_ **86** , 022308 (2012).
Article ADS  Google Scholar
  57. Gross, J. A. Designing codes around interactions: the case of a spin. _Phys. Rev. Lett._ **127** , 010504 (2021).
Article ADS MathSciNet CAS PubMed  Google Scholar
  58. Gross, J. A., Godfrin, C., Blais, A. & Dupont-Ferrier, E. Hardware-efficient error-correcting codes for large nuclear spins. _Phys. Rev. Appl._ **22** , 014006 (2024).
Article CAS  Google Scholar
  59. Brock, B. et al. Data for ‘Quantum error correction of qudits beyond break-even’. _Zenodo_ <https://doi.org/10.5281/zenodo.15009817> (2025).

Download references
## Acknowledgements
We thank R. G. Cortiñas, J. Curtis, W. Dai, S. Hazra, A. Koottandavida, A. Miano, S. Puri, K. C. Smith and T. Tsunoda for helpful discussions. This research was sponsored by the Army Research Office (ARO; grant no. W911NF-23-1-0051), by the US Department of Energy (DoE), Office of Science, National Quantum Information Science Research Centers, Co-design Center for Quantum Advantage (contract no. DE-SC0012704) and by the Air Force Office of Scientific Research (AFOSR; award no. FA9550-19-1-0399). The views and conclusions contained in this document are those of the authors and should not be interpreted as representing the official policies, either expressed or implied, of the ARO, DoE, AFOSR or the US Government. The US Government is authorized to reproduce and distribute reprints for Government purposes notwithstanding any copyright notation herein. The use of fabrication facilities was supported by the Yale Institute for Nanoscience and Quantum Engineering and the Yale SEAS Cleanroom.
## Author information
Author notes
  1. Alec Eickbusch & Volodymyr V. Sivak
Present address: Google Quantum AI, Santa Barbara, CA, USA
  2. Michel H. Devoret
Present address: UC Santa Barbara Department of Physics and Google Quantum AI, Santa Barbara, CA, USA

### Authors and Affiliations
  1. Department of Applied Physics, Yale University, New Haven, CT, USA
Benjamin L. Brock, Shraddha Singh, Alec Eickbusch, Volodymyr V. Sivak, Andy Z. Ding, Luigi Frunzio, Steven M. Girvin & Michel H. Devoret
  2. Yale Quantum Institute, Yale University, New Haven, CT, USA
Benjamin L. Brock, Shraddha Singh, Alec Eickbusch, Volodymyr V. Sivak, Andy Z. Ding, Luigi Frunzio, Steven M. Girvin & Michel H. Devoret
  3. Department of Physics, Yale University, New Haven, CT, USA
Benjamin L. Brock, Shraddha Singh, Alec Eickbusch, Volodymyr V. Sivak, Andy Z. Ding, Luigi Frunzio, Steven M. Girvin & Michel H. Devoret

Authors
  1. Benjamin L. Brock
View author publications
Search author on:PubMedGoogle Scholar
  2. Shraddha Singh
View author publications
Search author on:PubMedGoogle Scholar
  3. Alec Eickbusch
View author publications
Search author on:PubMedGoogle Scholar
  4. Volodymyr V. Sivak
View author publications
Search author on:PubMedGoogle Scholar
  5. Andy Z. Ding
View author publications
Search author on:PubMedGoogle Scholar
  6. Luigi Frunzio
View author publications
Search author on:PubMedGoogle Scholar
  7. Steven M. Girvin
View author publications
Search author on:PubMedGoogle Scholar
  8. Michel H. Devoret
View author publications
Search author on:PubMedGoogle Scholar

### Contributions
B.L.B. conceived the experiment, performed the measurements and analysed the results. B.L.B. and S.S. developed the theory, with supervision from S.M.G. B.L.B. devised the generalized Pauli measurement protocols, with input from A.E. and S.S. V.V.S. provided the experimental set-up and wrote the reinforcement learning code. A.E., V.V.S., A.Z.D. and L.F. provided experimental support throughout the project. M.H.D. supervised the project. B.L.B. and M.H.D. wrote the manuscript, with feedback from all authors.
### Corresponding authors
Correspondence to Benjamin L. Brock or Michel H. Devoret.
## Ethics declarations
### Competing interests
L.F. is a founder and shareholder of Quantum Circuits, Inc. S.M.G. is an equity holder in and receives consulting fees from Quantum Circuits, Inc. M.H.D. has an advisory role at Google Quantum AI. The other authors declare no competing interests.
## Peer review
### Peer review information
_Nature_ thanks the anonymous reviewers for their contribution to the peer review of this work.
## Additional information
**Publisher’s note** Springer Nature remains neutral with regard to jurisdictional claims in published maps and institutional affiliations.
## Supplementary information
### Supplementary Information
Supplementary sections I–VII, Figs. 1–19 and Tables I–VIII.
## Rights and permissions
**Open Access** This article is licensed under a Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License, which permits any non-commercial use, sharing, distribution and reproduction in any medium or format, as long as you give appropriate credit to the original author(s) and the source, provide a link to the Creative Commons licence, and indicate if you modified the licensed material. You do not have permission under this licence to share adapted material derived from this article or parts of it. The images or other third party material in this article are included in the article’s Creative Commons licence, unless indicated otherwise in a credit line to the material. If material is not included in the article’s Creative Commons licence and your intended use is not permitted by statutory regulation or exceeds the permitted use, you will need to obtain permission directly from the copyright holder. To view a copy of this licence, visit <http://creativecommons.org/licenses/by-nc-nd/4.0/>.
Reprints and permissions
## About this article

### Cite this article
Brock, B.L., Singh, S., Eickbusch, A. _et al._ Quantum error correction of qudits beyond break-even. _Nature_ **641** , 612–618 (2025). https://doi.org/10.1038/s41586-025-08899-y
Download citation
  * Received: 02 October 2024
  * Accepted: 14 March 2025
  * Published: 14 May 2025
  * Version of record: 14 May 2025
  * Issue date: 15 May 2025
  * DOI: https://doi.org/10.1038/s41586-025-08899-y

### Share this article
Anyone you share the following link with will be able to read this content:
Get shareable link
Sorry, a shareable link is not currently available for this article.
Copy shareable link to clipboard
Provided by the Springer Nature SharedIt content-sharing initiative 
### Subjects
  * Quantum information
  * Quantum mechanics

## This article is cited by
  * ###   Quantum control of an oscillator with a Kerr-cat qubit 
    * Andy Z. Ding
    * Benjamin L. Brock
    * Michel H. Devoret
_Nature Communications_ (2025)
  * ###   Single-qudit quantum neural networks for multiclass classification 
    * Leandro C. Souza
    * Renato Portugal
_Quantum Information Processing_ (2025)

Close banner Close
!Nature Briefing
Sign up for the _Nature Briefing_ newsletter — what matters in science, free to your inbox daily.
Close banner Close
Get the most important science stories of the day, free in your inbox. Sign up for Nature Briefing 
  *[DOI]: Digital Object Identifier
