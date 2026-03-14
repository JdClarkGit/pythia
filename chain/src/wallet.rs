//! Wallet loading from private key.

use alloy::{
    network::EthereumWallet,
    signers::local::PrivateKeySigner,
};
use anyhow::{anyhow, Context, Result};

/// Load a wallet from a hex-encoded private key string.
///
/// Accepts keys with or without the ``0x`` prefix.
///
/// # Arguments
/// * `private_key` - Hex-encoded 32-byte private key.
///
/// # Returns
/// An [`EthereumWallet`] ready for signing transactions.
pub fn load_wallet(private_key: &str) -> Result<EthereumWallet> {
    let key = private_key.strip_prefix("0x").unwrap_or(private_key);
    let signer: PrivateKeySigner = key
        .parse()
        .map_err(|e| anyhow!("Invalid private key: {e}"))?;
    Ok(EthereumWallet::from(signer))
}

/// Load wallet from the ``PRIVATE_KEY`` environment variable.
///
/// # Errors
/// Returns an error if the env var is missing or the key is malformed.
pub fn load_wallet_from_env() -> Result<EthereumWallet> {
    let key = std::env::var("PRIVATE_KEY")
        .context("PRIVATE_KEY environment variable not set")?;
    load_wallet(&key)
}
