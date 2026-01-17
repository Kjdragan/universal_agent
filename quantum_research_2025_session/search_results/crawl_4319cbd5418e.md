---
title: "An 11-qubit atom processor in silicon | Nature"
source: https://www.nature.com/articles/s41586-025-09827-w
date: 2025-05-15
description: "Phosphorus atoms in silicon represent a promising platform for quantum computing, as their nuclear spins exhibit coherence times over seconds1,2 with high-fidelity readout and single-qubit control3. B"
word_count: 7073
---

## Your privacy, your choice
We use essential cookies to make sure the site can function. We also use optional cookies for advertising, personalisation of content, usage analysis, and social media, as well as to allow video information to be shared for both marketing, analytics and editorial purposes.
By accepting optional cookies, you consent to the processing of your personal data - including transfers to third parties. Some third parties are outside of the European Economic Area, with varying standards of data protection.
See our privacy policy for more information on the use of your personal data.
Manage preferences for further information and to change your choices.
Accept all cookies Reject optional cookies
Skip to main content
Thank you for visiting nature.com. You are using a browser version with limited support for CSS. To obtain the best experience, we recommend you use a more up to date browser (or turn off compatibility mode in Internet Explorer). In the meantime, to ensure continued support, we are displaying the site without styles and JavaScript.
An 11-qubit atom processor in silicon 
 Download PDF 
 Download PDF 
## Abstract
Phosphorus atoms in silicon represent a promising platform for quantum computing, as their nuclear spins exhibit coherence times over seconds1."),2.") with high-fidelity readout and single-qubit control3."). By placing several phosphorus atoms within a radius of a few nanometres, they couple by means of the hyperfine interaction to a single, shared electron. Such a nuclear spin register enables high-fidelity multi-qubit control4.") and the execution of small-scale quantum algorithms5."). An important requirement for scaling up is the ability to extend high-fidelity entanglement non-locally across several spin registers. Here we address this challenge with an 11-qubit atom processor composed of two multi-nuclear spin registers that are linked by means of electron exchange interaction. Through the advancement of calibration and control protocols, we achieve single-qubit and multi-qubit gates with all fidelities ranging from 99.10% to 99.99%. By entangling all combinations of local and non-local nuclear-spin pairs, we map out the performance of the processor and achieve state-of-the-art Bell-state fidelities of up to 99.5%. We then generate Greenberger–Horne–Zeilinger (GHZ) states with an increasing number of qubits and show entanglement of up to eight nuclear spins. By establishing high-fidelity operation across interconnected nuclear spin registers, we realize a key milestone towards fault-tolerant quantum computation with atom processors.
### Similar content being viewed by others

###  Grover’s algorithm in a four-qubit silicon processor above the fault-tolerant threshold 
Article Open access 20 February 2025

###  Precision tomography of a three-qubit donor quantum processor in silicon 
Article 19 January 2022

###  High-fidelity initialization and control of electron and nuclear spins in a four-qubit register 
Article Open access 07 February 2024
## Main
The predominant material in modern classical computers, silicon, is also a strong contender for the practical implementation of quantum processors3."),6."),7."),8."). To unlock the promised computational benefits of quantum computing, the qubit count needs to scale while maintaining high operation fidelity and connectivity. In terms of qubit numbers, the lead is at present held by superconducting9."),10."), ion-trap11.") and neutral-atom12.") processors, which approach hundreds of interconnected qubits. Further scale-up faces platform-specific challenges related to manufacturing, control-systems miniaturization and materials engineering. In this context, silicon quantum processors are emerging as a promising platform owing to their small footprint and materials compatibility with industrial manufacturing8."),13."),14.").
In semiconductor devices, the number of individual qubits is increasing, with gate-defined arrays hosting up to 16 quantum dots14."),15."). So far, however, no more than four interconnected spin qubits were used in the execution of quantum circuits owing to challenges associated with multi-qubit control16."),17."),18."),19."). In this context, quantum computing with precision-placed phosphorus atoms in silicon, which we refer to as the 14|15 platform (according to the respective positions in the periodic table), is attracting growing interest driven by industry-leading physical-level metrics3.") with exceptional, second-long coherence times2."),20."). The 14|15 platform uses precision manufacturing21.") to place individual phosphorus atoms in close proximity (≲3 nm) to each other, in which a single loaded electron exhibits a hyperfine interaction with several nuclei. Such spin registers provide a unique set of advantages: the shared electron naturally acts as an ancilla qubit enabling quantum non-demolition (QND) readout of the nuclear spins and native multi-qubit (Toffoli) gates4."),5."). Combined with recent advances in silicon purification with sub-200 ppm of 29Si (ref. 22.")), these features enabled nuclear–nuclear CZ operations with fidelities exceeding 99% and the execution of three-qubit algorithms on a single multi-spin register5.").
To enable the scaling of the 14|15 platform, it is essential to develop fast interconnects between quantum processing nodes without compromising performance23."). The coupling of spin qubits is achievable by various mechanisms, such as dipolar interaction24.") or spin–photon conversion in superconducting cavities25."). The fastest coupling mechanism is provided by exchange interaction, as demonstrated with a 0.8-ns \\(\sqrt{{\rm{SWAP}}}\\) gate between atomic qubits in natural silicon26."). Exchange gates on electron spins have also been implemented with gate-defined quantum dots in isotopically pure silicon with fidelities greater than 99% (refs. 27."),28."),29."),30.")). Successful implementation of exchange gates in atom qubits have already been achieved in purified silicon-28 (ref. 31.")), yet the limited two-qubit gate fidelity challenges the applicability of quantum-error-correction protocols32."),33.").
Here we report a precision-placed 11-qubit atom processor in isotopically purified silicon-28 that runs on a fast and efficient exchange-based link. Compared with the previous atom-based implementations with nuclear spin qubits4."),5."),22."), we triple the number of coupled data qubits while maintaining the performance of single-qubit and two-qubit gates well above 99% fidelity. This achievement is enabled by systematic investigations of qubit stability, contextual errors and crosstalk, which informed the development of scalable calibration and control protocols. After outlining the basic set-up of the 11-qubit atom processor, we report the key metrics of single-qubit and two-qubit gates, assess pairwise entanglement for all combinations of nuclear spins and benchmark all-to-all connectivity through multi-qubit entanglement.
The connectivity of the nuclei and electrons both within each register and across registers is central to the operation of the 11-qubit atom processor (Fig. 1a). Each spin register contains nuclei (_n_ 1– _n_ 4 and _n_ 5– _n_ 9) that are hyperfine-coupled to a common electron (_e_ 1 and _e_ 2). Notably, these electrons are also exchange-coupled to each other, enabling non-local connectivity across the registers (Fig. 1b). The strength of electron exchange coupling _J_ is tunable by the voltage detuning _ε_ across in-plane control gates (Fig. 1c and Supplementary Information Section I). The Hamiltonian of the system is described in Supplementary Information Section II. Here we operate in a weak exchange-coupled regime with _J_ ≈ 1.55 MHz (Fig. 1c). In this regime, the controlled rotations (CROT) on the electron are less susceptible to charge noise and not conditional on the nuclear spins in the other register26."),34."),35."),36."). We note that the CROT operation on the electron spin has the advantage of implementing a native multi-qubit Toffoli gate that is conditional on the nuclear spins.
**Fig. 1: Single-qubit characteristics of the 11-qubit atom processor.**

**a** , Connectivity of nuclear spins (_n_ 1– _n_ 9) and electron spins (_e_ 1 and _e_ 2) through hyperfine and exchange coupling with energies in MHz. **b** , Scanning tunnelling micrograph of the processor core after hydrogen lithography showing the 4P register hosting _n_ 1– _n_ 4 and _e_ 1 (square) and the 5P register hosting _n_ 5– _n_ 9 and _e_ 2 (pentagon). The distance 13(1) nm (centre to centre) between the nuclear spin registers is atomically engineered to enable exchange coupling26."),54."). Scale bar, 10 nm. **c** , Exchange-coupled ESR spectrum of _e_ 2 as a function of voltage detuning _ε_ with indications on the resonance frequencies corresponding to CROT and zCROT. **d** , Rabi oscillations along one period _T_ Rabi for all spins of the processor. We measure the spin-up probability of the nucleus _P_ ⇑ (electron _P_ _↑_) as a function of the coherent NMR (ESR) drive duration. **e** , Phase coherence times measured for each spin through Ramsey (open symbols) and Hahn-echo (filled symbols) measurements. **f** , 1Q-RB results for each qubit showing average physical gate fidelities. SET, single-electron transistor.
Full size image
The initial calibration of the 11-qubit atom processor requires the characterization of 24 + 25 = 48 electron spin resonances (ESRs), which is doubled to 96 in the presence of electron exchange interaction. Analysing the stability of the ESR peaks (Supplementary Information Section III), we find that the frequencies within each register shift collectively. Accordingly, we can implement an efficient recalibration protocol that scales linearly with the number of coupled spin registers. By characterizing the ESR frequency for a single reference configuration of the nuclear spins, we infer the exact positions of all other ESR transitions of the register from the frequency offsets of the initial calibration. As a result, recalibrating all 96 ESR frequencies requires only two measurements, that is, one per register.
The state of the individual nuclear spins is controlled using nuclear magnetic resonance (NMR), similar to molecules in solution37.") and nitrogen-vacancy centres in diamond38."),39."). The readout of an individual nuclear spin is performed through QND readout using the ancillary electron (Supplementary Information Section IV). For nuclear spin initialization, we combine this ESR-based approach with conditional NMR π pulses (Supplementary Information Section V). To maximize the fidelity of the initialized state, we perform QND readout of the nuclear spin configuration of each register before each experiment and apply post-selection on the desired nuclear spin configuration (Supplementary Information Section VI). For all experiments, unless stated otherwise, spectator qubits—that is, spins not actively involved in a given gate or quantum circuit—are initialized in the ⇓ state, and spin manipulations are performed conditional on these initialized states. The large contrast observed in Rabi oscillations (Fig. 1d) across all data qubits shows the performance of the nuclear-spin readout and initialization procedure.
The coherence times for both nuclear and electron spins are characterized by means of Ramsey and Hahn-echo measurements (Fig. 1e). For the nuclear spins, the phase coherence time extracted from Ramsey measurements, \\({T}_{2}^{\star }\\), ranges from 1 to 46 ms. Refocusing with Hahn echo greatly extends such a phase coherence, \\({T}_{2}^{{\rm{Hahn}}}\\), to values between 3 and 660 ms. We observe that the phase coherence of the data qubits is related to its hyperfine Stark coefficient (Supplementary Information Section VII). Accordingly, we note that deterministic atom placement will provide a way to improve coherence by tailoring the spin registers for smaller susceptibility to electric field fluctuations. For the electrons _e_ 1 and _e_ 2, we measure similar phase coherence times of \\({T}_{2}^{\star }\approx 20\,{\rm{\mu }}{\rm{s}}\\) and \\({T}_{2}^{{\rm{Hahn}}}\approx 350\,{\rm{\mu }}{\rm{s}}\\). Overall, our investigations affirm the potential of refocusing techniques to substantially improve the performance of our 11-qubit atom processor.
Single-qubit randomized benchmarking (1Q-RB) reveals that all qubits except _n_ 4 operate with gate fidelities greater than 99.90% and as high as 99.99% for _n_ 5 (see Supplementary Information Section VIII for optimization details). We attribute this excellent performance to long coherence times and minimal frequency drifts in both ESR and NMR (Supplementary Information Sections III and VII). These single-qubit metrics are on par with our recent results using a single spin register5."), indicating consistency in atomic-scale fabrication.
To perform multi-qubit operations with any data qubit across our atom processor, we now establish a quantum link between the nuclear spin registers through the exchange interaction of the electrons. We first assess the performance of this link with interleaved two-qubit randomized benchmarking (2Q-RB) of the electron CROT gate (see Methods). To minimize off-resonant population transfer between the zero-controlled rotation (zCROT) and CROT resonances, the Rabi frequency is optimized to _f_ Rabi ≈ 400 kHz for the chosen exchange coupling _J_ ≈ 1.55 MHz (ref. 40.")) (Supplementary Information Section VIII.C). This choice sets the duration of the CROT π rotation to approximately 1.25 μs. Also, we calibrate the phase offsets of the CROT gates and implement a compensation protocol30.") (Supplementary Information Section VIII.C). Figure 2a shows the reference and interleaved 2Q-RB data for _e_ 2 when all nuclear spins are initialized to down (⇓⇓⇓⇓, ⇓⇓⇓⇓⇓), which we denote for simplicity as (⇓4, ⇓5). The extracted electron–electron CROT gate fidelity of 99.64(8)% indicates excellent performance that is relevant for the application of quantum-error-correction protocols.
**Fig. 2: High-fidelity two-qubit operation between nuclear (CZ) and electron (CROT) spins.**

**a** , Normalized 2Q-RB of the electron–electron CROT gate from the reference (black) and interleaved procedure (CROT  _e_ 2) showing the Clifford fidelity. **b** , Normalized 2Q-RB of the geometric CZ operation on the nuclear-spin pair _n_ 6 and _n_ 9 from the reference (black) and interleaved procedure (CZ). All other nuclear spins are initialized to down ⇓ in this experiment. **c** , Summary of the nuclear (CZ) and electron 2Q-RB (zCROT and CROT of _e_ 1 and _e_ 2) fidelities. For the electron CROT gate, the primitive fidelities (reference for interleaved 2Q-RB) are also shown for different nuclear spin configurations, with the corresponding frequency gap Δ _E_ _z_ = |_f_ CROT e1 −  _f_ CROT e2| indicated at the top.
Full size image
According to ref. 35."), the fidelity of the two-qubit CROT gate depends on the Larmor-frequency splitting Δ _E_ _z_ = |_f_ CROT e1 −  _f_ CROT e2|, which is defined by the nuclear-spin configuration. In particular, when Δ _E_ _z_ is similar to the exchange interaction strength _J_ , the fidelity is lower owing to hybridization with the singlet–triplet eigenbasis. By choosing small exchange of _J_ = 1.55 MHz, we operate at a large Δ _E_ _z_ /_J_ ratio and obtain CROT gate fidelities exceeding 99% across different nuclear-spin configurations, as shown in Fig. 2c.
A key task of the ancilla electron in our 14|15 platform is to entangle nuclear data qubits by means of a geometric CZ gate that is implemented through a 2π-ESR rotation4."),5."),41.") (for a detailed derivation, see Supplementary Information Section II). Figure 2b shows interleaved 2Q-RB results for the nuclear CZ gate applied on two nuclear spins _n_ 6 and _n_ 9 on the 5P register, giving a nuclear two-qubit-gate fidelity of 99.90(4)%. The nuclear CZ gate strongly outperforms the CROT gate and thus allows local multi-qubit operation on a spin register with high fidelity.
Before applying this electron-exchange-based link to entangle nuclear spins across the two registers, we first benchmark the generation of local Bell states within a single spin register. As an example, we entangle the nuclear spins _n_ 6 and _n_ 9 of the 5P register through the electron _e_ 2 (see schematic in Fig. 3a). An exemplary quantum circuit to prepare the Bell state is shown in Fig. 3b, which uses this nuclear CZ gate to entangle the nuclear-spin pair. Accordingly, the four maximally entangled Bell states, \\(| {\Phi }^{\pm }\rangle =(| \Downarrow \Downarrow \rangle \pm | \Uparrow \Uparrow \rangle )/\sqrt{2}\\) and \\(| {\Psi }^{\pm }\rangle =(| \Downarrow \Uparrow \rangle \pm | \Uparrow \Downarrow \rangle )/\sqrt{2}\\) can be generated by adjusting the phase of the initial −Y/2 NMR pulses, by inverting their respective signs. We remind that the gate operations used are conditional on the spectator data qubits in the system, which are initialized to ⇓. We perform quantum state tomography (QST) using a complete set of nine projections (all combinations of X, Y and Z for the two data qubits) and reconstruct the corresponding density matrix (Methods and Fig. 3c). Here the experiments are performed with _J_ ≈ 1.69 MHz, which sets the optimal Rabi frequency for CROT operations to 436 kHz. Without removal of state preparation and measurement (SPAM) errors, we obtain an average state fidelity of 99.2(3)% for all Bell states (see table in Fig. 3c). To characterize the local Φ+ state across all nuclear-spin pairs from the two registers, we reconstruct the density matrix from a reduced set of three projections (XX, YY and ZZ). This way, we can increase the measurement speed with similar accuracy42.") (Supplementary Information Section IX). For nuclear spins with smaller hyperfine coupling than _J_ , we reduce _f_ Rabi for CROT gates to minimize off-resonance driving (Supplementary Information Section VIII.C). Figure 3d shows the local Φ+ state fidelities for all local combinations of data qubits on the respective registers ranging from 91.4(5)% to 99.5(1)%. To the best of our knowledge, the peak Bell-state fidelity surpassing 99% represents the highest value reported in semiconductor devices so far.
**Fig. 3: Bell states within a register (left, local) and across registers (right, non-local).**

**a** (**e**), Connectivity of a local (non-local) Bell state. **b** (**f**), Circuit for generation and measurement of a Φ+ Bell state using local (non-local) CZ gate and QST. Open (filled) circles indicate whether the operation is conditional on the down (up) state. **c** (**g**), Reconstructed density matrix for a local (non-local) Φ+ Bell state. The table shows the fidelities for all local (non-local) Bell states. Here a complete set of nine projections is used to reconstruct the density matrix. **d** (**h**), Generation fidelities of local (non-local) Φ+ Bell state for all combinations of nuclear spins. As we use a reduced set of three projections, small deviations in the generation fidelities occur.
Full size image
The variation in Bell-state fidelities observed arises from the interplay between several effects, including the Stark coefficient, the operational speed, the stability of the qubit frequencies, microwave-induced frequency shifts and the coherence time of the qubits involved (compare Supplementary Information Sections III, VII and VIII). For instance, Bell states involving _n_ 5 exhibit lower fidelities (see the corresponding row in Fig. 3d). This reduction is primarily caused by its small hyperfine coupling, which sets the CROT gate speed approximately three times slower than for the other nuclear spins (_n_ 6– _n_ 9) in the same register (Supplementary Information Section VIII.D).
As a next step, we now interconnect the spin registers and implement non-local Bell states over the electron-exchange-based link. To demonstrate the approach, we entangle nuclear spins _n_ 4 and _n_ 9 through both electrons _e_ 1 and _e_ 2 (see connectivity in Fig. 3e). To implement the non-local CZ gate in the regime in which _J_ ≪ Δ _E_ _z_ , we project the targeted nuclear state on the electron _e_ 1 through X gates (π rotation), sandwiching the 2X operation on _e_ 2 (see circuit in Fig. 3f for the example of the Φ+ state). Again, we perform QST using a complete set of nine projections to maximize measurement accuracy. Figure 3g shows the density matrix of the non-local Bell state Φ+ with a table listing the extracted fidelities for Φ+, Φ−, Ψ+ and Ψ−, with an average of 97.2(9)%.
We characterize the non-local Φ+ state for all combinations of nuclear-spin pairs across the registers. Figure 3h shows the obtained state fidelities ranging from 87.0(4)% to 97.0(2)%. The observed reduction in fidelity compared with local Bell states is primarily attributed to the increased operation time of the non-local CZ gate. In particular, entanglement involving nuclear spins with smaller hyperfine coupling (_n_ 1, _n_ 2 or _n_ 5) exhibits slightly lower performance, underscoring the importance of engineering hyperfine couplings larger than the exchange strength _J_ in future devices. These results demonstrate the ability to generate pairwise entanglement between arbitrary nuclear-spin pairs, highlighting the potential of the 14|15 platform to realize efficient all-to-all connectivity.
A straightforward approach to benchmarking the all-to-all connectivity of a quantum processor is the generation of GHZ states with an increasing number of qubits. Accordingly, we investigate in the following non-local multi-qubit entanglement with an increasing number of nuclear spins. First, we generate a GHZ state with three nuclear spins: _n_ 4 on the 4P register and _n_ 6 and _n_ 9 on the 5P register (Fig. 4a). We implement a combination of local and non-local Bell states and concatenate the corresponding entanglement circuits as shown in Fig. 4b. The density matrix shown in Fig. 4c is reconstructed from a full set of QST measurements. Without SPAM removal, we report a GHZ state fidelity of 90.8(3)%.
**Fig. 4: Non-local multi-qubit GHZ states.**

**a** , Connectivity of the three-qubit GHZ state comprising _n_ 4, _n_ 6 and _n_ 9. **b** , Circuit for the generation and measurement of the three-qubit GHZ state using the local and non-local CZ gate and QST through the ancilla qubits _e_ 1 and _e_ 2. Open (filled) circles indicate whether the operation is conditional on the down (up) state. **c** , Reconstructed density matrix for the GHZ state with _N_ = 3 entangled nuclear spins. **d** , Normalized QST counts in the _z_ projection—that is, diagonal of density matrix—for GHZ states with increasing qubit count _N_. The bars on the left (right) show matrix elements in which all nuclear spins are down ⇓…⇓ (up ⇑…⇑). All other elements with mixed states (with ⇓ and ⇑) are combined via their sum in the bars in the middle. **e** , Generation fidelity as a function of the number of qubits _N_ in the GHZ state.
Full size image
To prepare a GHZ state with more than three qubits, we now extend the circuit shown in Fig. 4b by adding the local entanglement sequence—NMR −Y/2, local ESR 2X and NMR Y/2—for each extra qubit. For the 5P (4P) register, we add these local entanglement operations before (after) the non-local CZ. Because the number of tomography bases grows exponentially (3 _N_ , in which _N_ is the number of qubits), we use a reduced measurement strategy that requires only _N_ + 1 bases to estimate the state fidelity42."),43."). Figure 4d shows the counts in the _z_ basis of GHZ states with an increasing number of entangled nuclear spins. In the ideal GHZ state, measurement outcomes are equally distributed between the states in which all nuclear spins are either down (⇓…⇓) or up (⇑…⇑). Increasing the number of qubits in the GHZ state (_N_), we observe a gradual increase in the probability of all other states, that is, mixed combinations of ⇓ and ⇑. The corresponding GHZ fidelities are plotted in Fig. 4e. The three-qubit GHZ fidelity is 92(2)%, consistent with the value of 90.8(3)% obtained from full QST. Because a fidelity greater 50% is sufficient to witness genuine _N_ -qubit entanglement44."), the data demonstrate that entanglement is maintained for up to eight nuclear spins. Further performance improvements are anticipated by coherent control optimization45."), frequency crosstalk mitigation and the incorporation of refocusing pulses. Building on this progress, the present results demonstrate efficient connectivity across nuclear data qubits in our atom processor, representing an important step towards future implementations of quantum error correction on the 14|15 platform.
## Conclusion
By coupling a 4P and a 5P register by means of electron exchange interaction, we considerably exceeded the number of interconnected qubits with respect to previous works in semiconductor devices4."),5."),16."),17."),18."),19.") and achieve an important milestone towards a modular spin qubit system within the 14|15 platform. While increasing the number of connected qubits, we have shown that physical-level benchmarks are maintained and some of them even improved, with two-qubit gate fidelities reaching 99.9% for the first time in silicon qubits. Systematic characterization of the 11-qubit atom processor enabled the development of tailored calibration routines that scale linearly with more registers. By using the electron spin on each of the two registers as an ancilla qubit, we implemented efficient single-qubit and multi-qubit control for all nuclear spins. This level of performance has allowed us to entangle every nuclear-spin pair within the 11-qubit system with Bell-state fidelities ranging from 91.4(5)% to 99.5(1)% within registers and from 87.0(4)% to 97.0(2)% across registers. We expanded the connectivity by preparing multi-qubit GHZ states across all data qubits and showed that entanglement is preserved for up to eight nuclear spins. By successfully introducing a coherent link across spin registers while maintaining excellent qubit performance, we demonstrate a key capability for future implementations in the 14|15 platform aimed at quantum error correction.
In the present work, gate operations are performed under the assumption that spectator qubits remain in a pre-initialized state. Future work will focus on benchmarking performance with arbitrary spectator qubit states46."), including characterization of error and leakage channels using modified randomized benchmarking protocols47."),48."),49."), gate-set tomography50.") and non-Markovian process tomography51."). As implementing a universal geometric CZ gate requires driving all ESR transitions conditional on both ⇑ and ⇓ states of the spectator nuclear spins, we will pursue control optimization through pulse shaping and parallelized drive execution45."), alongside refined calibration strategies to mitigate microwave-induced frequency shifts52."). Finally, as small hyperfine couplings limit gate speed, we aim to atomically engineer the registers to optimize hyperfine couplings in future processors53.").
## Methods
### Experimental set-up
A single-electron transistor serves as charge reservoir and sensor enabling spin readout of the electrons through spin-to-charge conversion. Details of the basic operation of our atom processor are provided in Supplementary Information Section I. The encapsulation is about 45 nm. On top of the chip, an antenna is horizontally offset from the dots by about 300 nm (refs. 5."),55.")). It allows us to drive NMR and ESR. The experiment is performed in a cryogen-free dilution refrigerator at a base temperature of about 16 mK. Spin polarization is activated by a magnetic field _B_ ≈ 1.39 T along the [110] crystal direction.
### Randomized benchmarking
For 1Q-RB, we generate ten variations of a random set of Cliffords up to 1,024 gates. Each Clifford gate is chosen from the one-qubit Clifford group containing 24 elements. Using the Euler decomposition, we translate each Clifford to a single native Y(_θ_) rotation sandwiched between two virtual Z(_θ_) gates. Because the latter operation is instantaneous owing to a change of reference frame, the average number of primitive gates per Clifford is exactly one. For each Clifford set, we take 200 (50) single-shot measurements for the electron (nuclei). We perform qubit frequency recalibrations every 12 runs (equivalent to a few minutes time intervals). We measure recovery probabilities _F_ _↑_(_n_) and _F_ _↓_(_n_) to both up and down states and fit the data points with _F_(_n_) =  _F_ _↑_(_n_) −  _F_ _↓_(_n_) with _F_(_n_) =  _A_ _p_ _n_ , in which _n_ is the sequence length, _A_ is the factor containing SPAM errors and _p_ is the depolarizing strength. The Clifford gate fidelity _F_ C, and hence the primitive gate fidelity _F_ P, is then extracted as _F_ C =  _F_ P = (1 +  _p_)/2. In all randomized benchmarking experiments, we calculate the error bars by bootstrapping resampling methods assuming a multinomial distribution30."),40.").
Similarly, for 2Q-RB, we typically generate 20 variations of a random set of Cliffords up to 256 gates. Each Clifford gate is chosen from the two-qubit Clifford group containing 11,520 elements56."). For the electron, we use the decomposition to CROT rotations as in ref. 40."), in which the average number of primitive gates, \\(\bar{n}\\), is 2.57. For nuclear spins, the native operations consist of a combination of π/2 NMR pulses for single-qubit rotations and 2π ESR pulses as CZ gates5."). Similar to 1Q-RB, we measure recovery probabilities to both _↑_ _↑_ and _↓_ _↓_ to account for SPAM errors. To extract the polarizing strength, we fit _F_(_n_) =  _F_ _↑_ _↑_(_n_) −  _F_ _↓_ _↓_(_n_) with _F_(_n_) =  _A_ _p_ _n_ as before. The corresponding Clifford and primitive gate fidelities are _F_ C = (1 + 3 _p_)/4 and \\({F}_{{\rm{P}}}=1-(1-{F}_{{\rm{C}}})/\bar{n}\\), respectively.
For the interleaved 2Q-RB, we insert the target Clifford after each random gate, effectively doubling the sequence length. We measure the recovery probabilities in the same manner as standard 2Q-RB and extract the interleaved polarizing strength _p_ i. Accordingly, we extract the interleaved gate fidelity using _F_ i = (1 + 3 _p_ i/_p_)/4. The standard deviation is calculated using the same bootstrapping resampling method and standard error propagation analysis.
### Quantum state tomography
To perform QST measurements, we add projection pulses for each qubit to the target basis {_x_ ,  _y_ ,  _z_} before readout. In particular, we apply −Y/2 (X/2) to project on _x_ (_y_). Because _z_ is our native basis, there is no need for any extra rotations.
For Bell-state and GHZ-state generation, we merge the projection pulse with the last Y/2 rotation. Accordingly, when projecting to _x_ , the rotations cancel each other and thus we remove them both. For projections to _y_ , we convert the sequence Y/2 + X/2 into −Z/2 + Y/2 according to the Euler decomposition, as the virtual Z rotation, which is implemented by a global phase shift, does not require a physical pulse.
The full QST is taken by projecting to all 3 _N_ basis, in which _N_ is the number of qubits involved. We perform 2,000 single-shot measurements per basis and apply post-selection to ensure successful nuclear spin initialization. The density matrix is reconstructed by performing a constrained Gaussian linear least-squares fit to the tomography counts. The standard deviation is then extracted from Monte Carlo bootstrapping resampling5."),40."),57.").
## Data availability
The raw data used in this article are available from Zenodo at <https://doi.org/10.5281/zenodo.15549984> (ref. 58.")).
## Code availability
The code used to analyse the data and produce the figures in this article is available from Zenodo at <https://doi.org/10.5281/zenodo.15549984> (ref. 58.")).
## References
  1. Kane, B. E. A silicon-based nuclear spin quantum computer. _Nature_ **393** , 133–137 (1998).
Article ADS CAS  Google Scholar
  2. Muhonen, J. T. et al. Storing quantum information for 30 seconds in a nanoelectronic device. _Nat. Nanotechnol._ **9** , 986–991 (2014).
Article ADS CAS PubMed  Google Scholar
  3. Stano, P. & Loss, D. Review of performance metrics of spin qubits in gated semiconducting nanostructures. _Nat. Rev. Phys._ **4** , 672–688 (2022).
Article  Google Scholar
  4. Mądzik, M. T. et al. Precision tomography of a three-qubit donor quantum processor in silicon. _Nature_ **601** , 348–353 (2022).
Article ADS PubMed  Google Scholar
  5. Thorvaldson, I. et al. Grover’s algorithm in a four-qubit silicon processor above the fault-tolerant threshold. _Nat. Nanotechnol._ **20** , 472–477 (2025).
Article ADS CAS PubMed PubMed Central  Google Scholar
  6. Burkard, G., Ladd, T. D., Pan, A., Nichol, J. M. & Petta, J. R. Semiconductor spin qubits. _Rev. Mod. Phys._ **95** , 025003 (2023).
Article ADS CAS  Google Scholar
  7. Takeda, K., Noiri, A., Nakajima, T., Kobayashi, T. & Tarucha, S. Quantum error correction with silicon spin qubits. _Nature_ **608** , 682–686 (2022).
Article ADS CAS PubMed PubMed Central  Google Scholar
  8. Neyens, S. et al. Probing single electrons across 300-mm spin qubit wafers. _Nature_ **629** , 80–85 (2024).
Article ADS CAS PubMed PubMed Central  Google Scholar
  9. Google Quantum AI and Collaborators. Quantum error correction below the surface code threshold. _Nature_ **638** , 920–926 (2024).
Article ADS  Google Scholar
  10. Arute, F. et al. Quantum supremacy using a programmable superconducting processor. _Nature_ **574** , 505–510 (2019).
Article ADS CAS PubMed  Google Scholar
  11. Paetznick, A. et al. Demonstration of logical qubits and repeated error correction with better-than-physical error rates. Preprint at <https://arxiv.org/abs/2404.02280> (2024).
  12. Bluvstein, D. et al. Logical quantum processor based on reconfigurable atom arrays. _Nature_ **626** , 58–65 (2023).
Article ADS PubMed PubMed Central  Google Scholar
  13. Zwerver, A. M. J. et al. Qubits made by advanced semiconductor manufacturing. _Nat. Electron._ **5** , 184–190 (2022).
Article  Google Scholar
  14. George, H. C. et al. 12-spin-qubit arrays fabricated on a 300 mm semiconductor manufacturing line. _Nano Lett._ **25** , 793–799 (2024).
Article ADS PubMed PubMed Central  Google Scholar
  15. Borsoi, F. et al. Shared control of a 16 semiconductor quantum dot crossbar array. _Nat. Nanotechnol._ **19** , 21–27 (2023).
Article ADS PubMed PubMed Central  Google Scholar
  16. Hendrickx, N. W. et al. A four-qubit germanium quantum processor. _Nature_ **591** , 580–585 (2021).
Article ADS CAS PubMed  Google Scholar
  17. Philips, S. G. J. et al. Universal control of a six-qubit quantum processor in silicon. _Nature_ **609** , 919–924 (2022).
Article ADS CAS PubMed PubMed Central  Google Scholar
  18. Weinstein, A. J. et al. Universal logic with encoded spin qubits in silicon. _Nature_ **615** , 817–822 (2023).
Article ADS CAS PubMed PubMed Central  Google Scholar
  19. Zhang, X. et al. Universal control of four singlet–triplet qubits. _Nat. Nanotechnol._ **20** , 209–215 (2024).
Article ADS CAS PubMed PubMed Central  Google Scholar
  20. Hsueh, Y.-L. et al. Hyperfine-mediated spin relaxation in donor-atom qubits in silicon. _Phys. Rev. Res._ **5** , 023043 (2023).
Article CAS  Google Scholar
  21. Fuechsle, M. et al. A single-atom transistor. _Nat. Nanotechnol._ **7** , 242–246 (2012).
Article ADS CAS PubMed  Google Scholar
  22. Reiner, J. et al. High-fidelity initialization and control of electron and nuclear spins in a four-qubit register. _Nat. Nanotechnol._ **19** , 605–611 (2024).
Article ADS CAS PubMed PubMed Central  Google Scholar
  23. Vandersypen, L. M. K. et al. Interfacing spin qubits in quantum dots and donors—hot, dense, and coherent. _npj Quantum Inf._ **3** , 34 (2017).
Article ADS  Google Scholar
  24. Sarkar, A. et al. Optimisation of electron spin qubits in electrically driven multi-donor quantum dots. _npj Quantum Inf._ **8** , 127 (2022).
Article ADS  Google Scholar
  25. Dijkema, J. et al. Cavity-mediated iSWAP oscillations between distant spins. _Nat. Phys._ **21** , 168–174 (2024).
Article PubMed PubMed Central  Google Scholar
  26. He, Y. et al. A two-qubit gate between phosphorus donor electrons in silicon. _Nature_ **571** , 371–375 (2019).
Article ADS CAS PubMed  Google Scholar
  27. Noiri, A. et al. Fast universal quantum gate above the fault-tolerance threshold in silicon. _Nature_ **601** , 338–342 (2022).
Article ADS CAS PubMed  Google Scholar
  28. Mills, A. R. et al. Two-qubit silicon quantum processor with operation fidelity exceeding 99%. _Sci. Adv._ **8** , eabn5130 (2022).
Article CAS PubMed PubMed Central  Google Scholar
  29. Xue, X. et al. Quantum logic with spin qubits crossing the surface code threshold. _Nature_ **601** , 343–347 (2022).
Article ADS CAS PubMed PubMed Central  Google Scholar
  30. Wu, Y.-H. et al. Hamiltonian phase error in resonantly driven CNOT gate above the fault-tolerant threshold. _npj Quantum Inf._ **10** , 8 (2024).
Article ADS  Google Scholar
  31. Stemp, H. G. et al. Tomography of entangling two-qubit logic operations in exchange-coupled donor electron spin qubits. _Nat. Commun._ **15** , 8415 (2024).
Article ADS CAS PubMed PubMed Central  Google Scholar
  32. Fowler, A. G., Mariantoni, M., Martinis, J. M. & Cleland, A. N. Surface codes: towards practical large-scale quantum computation. _Phys. Rev. A_ **86** , 032324 (2012).
Article ADS  Google Scholar
  33. Higgott, O., Bohdanowicz, T. C., Kubica, A., Flammia, S. T. & Campbell, E. T. Improved decoding of circuit noise and fragile boundaries of tailored surface codes. _Phys. Rev. X_ **13** , 031007 (2023).
CAS  Google Scholar
  34. Kalra, R., Laucht, A., Hill, C. D. & Morello, A. Robust two-qubit gates for donors in silicon controlled by hyperfine interactions. _Phys. Rev. X_ **4** , 021044 (2014).
CAS  Google Scholar
  35. Kranz, L. et al. High-fidelity CNOT gate for donor electron spin qubits in silicon. _Phys. Rev. Appl._ **19** , 024068 (2023).
Article ADS CAS  Google Scholar
  36. Stemp, H. G. et al. Scalable entanglement of nuclear spins mediated by electron exchange. _Science_ **389** , 1234–1238 (2025).
Article ADS MathSciNet CAS PubMed  Google Scholar
  37. Jones, J. A. Quantum computing with NMR. _Prog. Nucl. Magn. Reson. Spectrosc._ **59** , 91–120 (2011).
Article CAS PubMed  Google Scholar
  38. Waldherr, G. et al. Quantum error correction in a solid-state hybrid spin register. _Nature_ **506** , 204–207 (2014).
Article ADS CAS PubMed  Google Scholar
  39. Bradley, C. E. et al. A ten-qubit solid-state spin register with quantum memory up to one minute. _Phys. Rev. X_ **9** , 031045 (2019).
CAS  Google Scholar
  40. Huang, W. et al. Fidelity benchmarks for two-qubit gates in silicon. _Nature_ **569** , 532–536 (2019).
Article ADS CAS PubMed  Google Scholar
  41. Filidou, V. et al. Ultrafast entangling gates between nuclear spins using photoexcited triplet states. _Nat. Phys._ **8** , 596–600 (2012).
Article CAS  Google Scholar
  42. Gühne, O., Lu, C.-Y., Gao, W.-B. & Pan, J.-W. Toolbox for entanglement detection and fidelity estimation. _Phys. Rev. A_ **76** , 030305 (2007).
Article ADS  Google Scholar
  43. Moses, S. A. et al. A race-track trapped-ion quantum processor. _Phys. Rev. X_ **13** , 041052 (2023).
CAS  Google Scholar
  44. Gühne, O. & Seevinck, M. Separability criteria for genuine multiparticle entanglement. _New J. Phys._ **12** , 053002 (2010).
Article ADS  Google Scholar
  45. Wu, Y.-H. et al. Simultaneous high-fidelity single-qubit gates in a spin qubit array. Preprint at <https://arxiv.org/abs/2507.11918> (2025).
  46. Krinner, S. et al. Benchmarking coherent errors in controlled-phase gates due to spectator qubits. _Phys. Rev. Appl._ **14** , 024042 (2020).
Article ADS CAS  Google Scholar
  47. Wood, C. J. & Gambetta, J. M. Quantification and characterization of leakage errors. _Phys. Rev. A_ **97** , 032306 (2018).
Article ADS CAS  Google Scholar
  48. Andrews, R. W. et al. Quantifying error and leakage in an encoded Si/SiGe triple-dot qubit. _Nat. Nanotechnol._ **14** , 747–750 (2019).
Article ADS CAS PubMed  Google Scholar
  49. Boixo, S. et al. Characterizing quantum supremacy in near-term devices. _Nat. Phys._ **14** , 595–600 (2018).
Article CAS  Google Scholar
  50. Nielsen, E. et al. Gate set tomography. _Quantum_ **5** , 557 (2021).
Article  Google Scholar
  51. White, G. A. L., Hill, C. D., Pollock, F. A., Hollenberg, L. C. L. & Modi, K. Demonstration of non-Markovian process characterisation and control on a quantum processor. _Nat. Commun._ **11** , 6301 (2020).
Article ADS CAS PubMed PubMed Central  Google Scholar
  52. Undseth, B. et al. Hotter is easier: unexpected temperature dependence of spin qubit frequencies. _Phys. Rev. X_ **13** , 041015 (2023).
CAS  Google Scholar
  53. Schofield, S. R. et al. Roadmap on atomic-scale semiconductor devices. _Nano Futures_ **9** , 012001 (2025).
Article ADS CAS  Google Scholar
  54. Kranz, L. et al. The use of exchange coupled atom qubits as atomic-scale magnetic field sensors. _Adv. Mater._ **35** , 2201625 (2023).
Article CAS  Google Scholar
  55. Hile, S. J. et al. Addressable electron spin resonance using donors and donor molecules in silicon. _Sci. Adv._ **4** , eaaq1459 (2018).
Article ADS PubMed PubMed Central  Google Scholar
  56. Barends, R. et al. Superconducting quantum circuits at the surface code threshold for fault tolerance. _Nature_ **508** , 500–503 (2014).
Article ADS CAS PubMed  Google Scholar
  57. Watson, T. F. et al. A programmable two-qubit quantum processor in silicon. _Nature_ **555** , 633–637 (2018).
Article ADS CAS PubMed  Google Scholar
  58. Edlbauer, H. & Wang, J. Data and analysis scripts of the publication “An 11-qubit atom processor in silicon”. _Zenodo_ <https://doi.org/10.5281/zenodo.15549983> (2025).

Download references
## Acknowledgements
The research outlined in this article was conducted and supported by the team at Silicon Quantum Computing Pty Ltd (ACN 619 102 608) and supported by investors, partners and stakeholders.
## Funding
Open access funding provided through UNSW Library.
## Author information
Author notes
  1. These authors contributed equally: Hermann Edlbauer, Junliang Wang
  2. These authors jointly supervised this work: Ludwik Kranz, Michelle Y. Simmons

### Authors and Affiliations
  1. Silicon Quantum Computing Pty Ltd, UNSW Sydney, Sydney, New South Wales, Australia
Hermann Edlbauer, Junliang Wang, A. M. Saffat-Ee Huq, Ian Thorvaldson, Michael T. Jones, Saiful Haque Misha, William J. Pappas, Christian M. Moehle, Yu-Ling Hsueh, Henric Bornemann, Samuel K. Gorman, Yousun Chung, Joris G. Keizer, Ludwik Kranz & Michelle Y. Simmons

Authors
  1. Hermann Edlbauer
View author publications
Search author on:PubMedGoogle Scholar
  2. Junliang Wang
View author publications
Search author on:PubMedGoogle Scholar
  3. A. M. Saffat-Ee Huq
View author publications
Search author on:PubMedGoogle Scholar
  4. Ian Thorvaldson
View author publications
Search author on:PubMedGoogle Scholar
  5. Michael T. Jones
View author publications
Search author on:PubMedGoogle Scholar
  6. Saiful Haque Misha
View author publications
Search author on:PubMedGoogle Scholar
  7. William J. Pappas
View author publications
Search author on:PubMedGoogle Scholar
  8. Christian M. Moehle
View author publications
Search author on:PubMedGoogle Scholar
  9. Yu-Ling Hsueh
View author publications
Search author on:PubMedGoogle Scholar
  10. Henric Bornemann
View author publications
Search author on:PubMedGoogle Scholar
  11. Samuel K. Gorman
View author publications
Search author on:PubMedGoogle Scholar
  12. Yousun Chung
View author publications
Search author on:PubMedGoogle Scholar
  13. Joris G. Keizer
View author publications
Search author on:PubMedGoogle Scholar
  14. Ludwik Kranz
View author publications
Search author on:PubMedGoogle Scholar
  15. Michelle Y. Simmons
View author publications
Search author on:PubMedGoogle Scholar

### Contributions
J.W., H.E. and A.M.S.-E.H. measured the device with the help of W.J.P. and C.M.M. under the supervision of L.K. I.T., Y.-L.H. and S.K.G. provided theoretical support to the measurements. M.T.J., S.H.M. and H.B. fabricated the device under the supervision of Y.C. and J.G.K. The manuscript was written by H.E. and J.W., with input from all authors. L.K. and M.Y.S. supervised the project.
### Corresponding author
Correspondence to Michelle Y. Simmons.
## Ethics declarations
### Competing interests
M.Y.S. is a director of the company Silicon Quantum Computing Pty Ltd. H.E., J.W., A.M.S.-E.H., I.T., M.T.J., S.H.M., W.J.P., C.M.M., Y.-L.H., H.B., S.K.G., Y.C., J.G.K., L.K. and M.Y.S. (all authors) declare equity interest in Silicon Quantum Computing Pty Ltd.
## Peer review
### Peer review information
_Nature_ thanks the anonymous reviewers for their contribution to the peer review of this work.
## Additional information
**Publisher’s note** Springer Nature remains neutral with regard to jurisdictional claims in published maps and institutional affiliations.
## Supplementary information
### Supplementary Information File
## Rights and permissions
**Open Access** This article is licensed under a Creative Commons Attribution 4.0 International License, which permits use, sharing, adaptation, distribution and reproduction in any medium or format, as long as you give appropriate credit to the original author(s) and the source, provide a link to the Creative Commons licence, and indicate if changes were made. The images or other third party material in this article are included in the article’s Creative Commons licence, unless indicated otherwise in a credit line to the material. If material is not included in the article’s Creative Commons licence and your intended use is not permitted by statutory regulation or exceeds the permitted use, you will need to obtain permission directly from the copyright holder. To view a copy of this licence, visit <http://creativecommons.org/licenses/by/4.0/>.
Reprints and permissions
## About this article

### Cite this article
Edlbauer, H., Wang, J., Huq, A.M.SE. _et al._ An 11-qubit atom processor in silicon. _Nature_ **648** , 569–575 (2025). https://doi.org/10.1038/s41586-025-09827-w
Download citation
  * Received: 15 May 2025
  * Accepted: 29 October 2025
  * Published: 17 December 2025
  * Version of record: 17 December 2025
  * Issue date: 18 December 2025
  * DOI: https://doi.org/10.1038/s41586-025-09827-w

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
