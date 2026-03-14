//! Shared types for the chain executor.

use alloy::primitives::{Address, FixedBytes, U256};
use serde::{Deserialize, Serialize};
use std::str::FromStr;
use anyhow::{anyhow, Result};

/// Contract addresses for Polygon mainnet.
#[derive(Debug, Clone)]
pub struct Addresses {
    /// Gnosis ConditionalTokens Framework contract.
    pub ctf: Address,
    /// Polymarket CTF Exchange contract.
    pub ctf_exchange: Address,
    /// USDC on Polygon.
    pub usdc: Address,
}

impl Addresses {
    /// Mainnet defaults.
    pub fn mainnet() -> Self {
        Self {
            ctf: "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
                .parse()
                .expect("CTF address"),
            ctf_exchange: "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
                .parse()
                .expect("CTFExchange address"),
            usdc: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
                .parse()
                .expect("USDC address"),
        }
    }

    /// Build from optional overrides (falls back to mainnet defaults).
    pub fn from_env(ctf: Option<&str>, usdc: Option<&str>) -> Result<Self> {
        let defaults = Self::mainnet();
        Ok(Self {
            ctf: ctf
                .map(|s| s.parse::<Address>())
                .transpose()
                .map_err(|e| anyhow!("Invalid CTF address: {e}"))?
                .unwrap_or(defaults.ctf),
            ctf_exchange: defaults.ctf_exchange,
            usdc: usdc
                .map(|s| s.parse::<Address>())
                .transpose()
                .map_err(|e| anyhow!("Invalid USDC address: {e}"))?
                .unwrap_or(defaults.usdc),
        })
    }
}

/// Result emitted to stdout as JSON by the binary.
#[derive(Debug, Serialize, Deserialize)]
pub struct ExecutionResult {
    /// Transaction hash on success.
    pub tx_hash: Option<String>,
    /// Error message on failure.
    pub error: Option<String>,
    /// Gas used (informational).
    pub gas_used: Option<u64>,
}

impl ExecutionResult {
    pub fn success(tx_hash: String, gas_used: u64) -> Self {
        Self {
            tx_hash: Some(tx_hash),
            error: None,
            gas_used: Some(gas_used),
        }
    }

    pub fn failure(error: String) -> Self {
        Self {
            tx_hash: None,
            error: Some(error),
            gas_used: None,
        }
    }

    pub fn print(&self) {
        println!("{}", serde_json::to_string(self).unwrap_or_default());
    }
}

/// Parse a hex condition ID (with or without 0x prefix) to bytes32.
pub fn parse_condition_id(s: &str) -> Result<FixedBytes<32>> {
    let s = s.strip_prefix("0x").unwrap_or(s);
    if s.len() != 64 {
        return Err(anyhow!(
            "condition_id must be 32 bytes (64 hex chars), got {} chars",
            s.len()
        ));
    }
    let bytes = hex::decode(s).map_err(|e| anyhow!("Invalid hex: {e}"))?;
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&bytes);
    Ok(FixedBytes::from(arr))
}
