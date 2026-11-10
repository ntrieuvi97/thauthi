"""
Thauthi 4.0: The Post-Developer Singularity Kernel for AI Coding Agents.

Combines the Centsible Reflex Kernel with 8 Autonomous Runtime & Compiler Pillars:
1. Omniscient Runtime Tracer & Causal Fault Localization (thauthi.omniscient)
2. In-Flight Self-Healing Immune System & Counterfactual AST Surgery (thauthi.immune)
3. Behavioral Invariant Miner & Differential Shadow Prover (thauthi.prover)
4. Neural-to-Symbolic AST Crystallizer (thauthi.crystallizer)
5. Algorithmic Alchemist: Autonomous O(N^2)->O(N) & O(2^N)->O(N) Optimizer (thauthi.alchemist)
6. Chronos: Deterministic I/O Virtualization & Time-Travel Replay Kernel (thauthi.chronos)
7. Contract-to-Code CEGIS Program Synthesizer (thauthi.synthesizer)
8. Whole-Repo Call-Graph Ripple Propagator (thauthi.hive)
"""

__version__ = "4.0.12"

from thauthi.alchemist import AlgorithmicAlchemist, TransmutationReport
from thauthi.chronos import ChronosTimeMachine, ExecutionCassette, SyscallEvent, time_travel
from thauthi.crystallizer import NeuralSymbolicCrystallizer, SymbolicCrystal
from thauthi.hive import CallGraphHive, CallSiteSurgicalFix, FunctionSignatureInfo, HivePropagationReport, RepoSymbolIndex
from thauthi.immune import AutopoieticImmuneSystem, CounterfactualSurgeon, immune
from thauthi.initializer import RepoInitializer
from thauthi.mcp_server import ThauthiMCPServer
from thauthi.omniscient import CausalFaultReport, FrameDNA, OmniscientTracer
from thauthi.prover import BehavioralContract, BehavioralInvariantMiner, DifferentialShadowProver
from thauthi.synthesizer import ContractSynthesizer, SynthesisResult, synthesize

__all__ = [
    "__version__",
    "OmniscientTracer",
    "FrameDNA",
    "CausalFaultReport",
    "AutopoieticImmuneSystem",
    "CounterfactualSurgeon",
    "immune",
    "BehavioralInvariantMiner",
    "BehavioralContract",
    "DifferentialShadowProver",
    "NeuralSymbolicCrystallizer",
    "SymbolicCrystal",
    "AlgorithmicAlchemist",
    "TransmutationReport",
    "ChronosTimeMachine",
    "ExecutionCassette",
    "SyscallEvent",
    "time_travel",
    "ContractSynthesizer",
    "SynthesisResult",
    "synthesize",
    "CallGraphHive",
    "FunctionSignatureInfo",
    "CallSiteSurgicalFix",
    "HivePropagationReport",
    "RepoSymbolIndex",
    "RepoInitializer",
    "ThauthiMCPServer",
]
