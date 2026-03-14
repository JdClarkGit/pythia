"""Capital management: sizing, Kelly allocation, recycling."""

from capital.allocator import KellyAllocator, AllocationResult
from capital.manager import CapitalManager

__all__ = ["KellyAllocator", "AllocationResult", "CapitalManager"]
