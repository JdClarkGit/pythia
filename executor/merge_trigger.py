"""On-chain merge trigger: shells out to the compiled Rust binary.

The Rust binary ``chain_executor`` handles:
  1. Approving the CTF contract to transfer your ERC-1155 tokens (if needed).
  2. Calling ``mergePositions()`` on the Gnosis ConditionalTokens contract.
  3. Returning the transaction hash on success or an error message.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# Default path to the compiled Rust binary
_DEFAULT_BINARY = str(
    Path(__file__).parent.parent / "chain" / "target" / "release" / "chain_executor"
)


class MergeTrigger:
    """Invokes the Rust ``chain_executor`` binary to call mergePositions().

    Args:
        binary_path: Path to the compiled Rust binary.
        rpc_url: Polygon RPC URL.
        private_key: Hex-encoded wallet private key.
        ctf_address: ConditionalTokens contract address.
        usdc_address: USDC contract address.
        dry_run: If ``True``, print the command without executing.
        timeout_seconds: Maximum time to wait for the binary to complete.
    """

    def __init__(
        self,
        binary_path: Optional[str] = None,
        rpc_url: str = "https://polygon-rpc.com",
        private_key: Optional[str] = None,
        ctf_address: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        usdc_address: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        dry_run: bool = False,
        timeout_seconds: int = 120,
    ) -> None:
        self._binary = binary_path or _DEFAULT_BINARY
        self._rpc = rpc_url
        self._pk = private_key or os.environ.get("PRIVATE_KEY", "")
        self._ctf = ctf_address
        self._usdc = usdc_address
        self._dry_run = dry_run
        self._timeout = timeout_seconds

    def binary_exists(self) -> bool:
        """Check whether the Rust binary is compiled and available.

        Returns:
            ``True`` if the binary file exists and is executable.
        """
        return shutil.which(self._binary) is not None or os.path.isfile(self._binary)

    async def merge(self, condition_id: str, shares: float) -> Optional[str]:
        """Trigger an on-chain mergePositions() call.

        Args:
            condition_id: The market's condition ID (bytes32 hex string).
            shares: Number of share-pairs to merge (float, converted to
                    6-decimal integer by the Rust binary).

        Returns:
            Transaction hash string on success, ``None`` on failure.
        """
        # Convert shares to 6-decimal integer (USDC has 6 decimals)
        amount_wei = int(shares * 1_000_000)

        cmd = [
            self._binary,
            "merge",
            "--condition-id", condition_id,
            "--amount", str(amount_wei),
            "--rpc", self._rpc,
            "--ctf", self._ctf,
            "--usdc", self._usdc,
        ]

        if self._dry_run:
            log.info("[DRY-RUN] Would execute: %s", " ".join(cmd))
            return "0xdryrun0000000000000000000000000000000000000000000000000000000000"

        if not self.binary_exists():
            log.error(
                "Rust binary not found at %s — did you run `cargo build --release`?",
                self._binary,
            )
            return None

        env = {**os.environ, "PRIVATE_KEY": self._pk}

        try:
            log.debug("Executing merge: %s", " ".join(cmd[1:]))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            log.error("chain_executor timed out (condition=%s)", condition_id)
            return None
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to launch chain_executor: %s", exc)
            return None

        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if proc.returncode != 0:
            log.error(
                "chain_executor exited %d | stderr: %s",
                proc.returncode,
                stderr_str[:500],
            )
            return None

        # Parse JSON output from the Rust binary
        try:
            result = json.loads(stdout_str)
            tx_hash = result.get("tx_hash")
            if tx_hash:
                log.info("mergePositions tx: %s", tx_hash)
                return str(tx_hash)
            log.error("chain_executor returned no tx_hash: %s", stdout_str)
            return None
        except json.JSONDecodeError:
            # Fall back: treat stdout as the raw tx hash
            if stdout_str.startswith("0x") and len(stdout_str) == 66:
                log.info("mergePositions tx: %s", stdout_str)
                return stdout_str
            log.error("Unexpected chain_executor output: %s", stdout_str[:200])
            return None

    async def approve_if_needed(
        self, yes_token_id: str, no_token_id: str, amount: int
    ) -> bool:
        """Approve the CTF to spend YES/NO tokens (ERC-1155 setApprovalForAll).

        Calls the Rust binary's ``approve`` sub-command.

        Args:
            yes_token_id: ERC-1155 YES token ID (decimal string).
            no_token_id: ERC-1155 NO token ID (decimal string).
            amount: Amount in smallest units (not used for setApprovalForAll,
                    but included for ABI consistency).

        Returns:
            ``True`` if approval succeeded or was already granted.
        """
        if self._dry_run:
            return True

        if not self.binary_exists():
            log.error("Rust binary not found — cannot approve")
            return False

        cmd = [
            self._binary,
            "approve",
            "--ctf", self._ctf,
            "--rpc", self._rpc,
        ]
        env = {**os.environ, "PRIVATE_KEY": self._pk}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )
        except Exception as exc:  # noqa: BLE001
            log.error("approve sub-command failed: %s", exc)
            return False

        if proc.returncode != 0:
            log.error(
                "approve exited %d: %s", proc.returncode, stderr.decode()[:300]
            )
            return False

        log.debug("Approval granted: %s", stdout.decode().strip())
        return True
