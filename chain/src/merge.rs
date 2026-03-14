//! On-chain merge logic: approve + mergePositions.

use alloy::{
    primitives::{Address, FixedBytes, U256},
    providers::{Provider, ProviderBuilder},
    signers::local::PrivateKeySigner,
    network::EthereumWallet,
};
use anyhow::{Context, Result};

use crate::contracts::{ConditionalTokens, IERC1155};
use crate::types::{Addresses, ExecutionResult, parse_condition_id};

/// Execute the full merge-arb settlement on Polygon.
///
/// Steps:
/// 1. Check/set ERC-1155 approval (setApprovalForAll) if not already granted.
/// 2. Call `mergePositions()` on the CTF contract.
/// 3. Return the transaction hash.
///
/// # Arguments
/// * `rpc_url`       - Polygon JSON-RPC endpoint.
/// * `wallet`        - Funded Polygon wallet.
/// * `addresses`     - Contract addresses struct.
/// * `condition_id`  - 32-byte condition ID hex string.
/// * `amount`        - Token amount in 6-decimal units (e.g. 1_000_000 = 1 share).
/// * `gas_multiplier`- Multiply estimated gas by this factor (e.g. 1.2).
pub async fn execute_merge(
    rpc_url: &str,
    wallet: EthereumWallet,
    addresses: &Addresses,
    condition_id_str: &str,
    amount: U256,
    gas_multiplier: f64,
) -> Result<ExecutionResult> {
    // Build provider
    let provider = ProviderBuilder::new()
        .with_recommended_fillers()
        .wallet(wallet)
        .on_http(rpc_url.parse().context("Invalid RPC URL")?);

    let signer_address = provider
        .get_accounts()
        .await
        .context("Failed to get accounts")?
        .into_iter()
        .next()
        .context("No accounts available")?;

    let condition_id: FixedBytes<32> = parse_condition_id(condition_id_str)
        .context("Invalid condition_id")?;

    let ctf = ConditionalTokens::new(addresses.ctf, &provider);

    // ── Step 1: Ensure approval ────────────────────────────────────────────
    let erc1155 = IERC1155::new(addresses.ctf, &provider);
    let is_approved = erc1155
        .isApprovedForAll(signer_address, addresses.ctf)
        .call()
        .await
        .map(|r| r._0)
        .unwrap_or(false);

    if !is_approved {
        tracing::info!("Granting setApprovalForAll on CTF …");
        let approve_tx = erc1155
            .setApprovalForAll(addresses.ctf, true)
            .send()
            .await
            .context("setApprovalForAll send failed")?;
        let approve_receipt = approve_tx
            .get_receipt()
            .await
            .context("setApprovalForAll receipt failed")?;
        tracing::info!(
            "Approval granted, tx={}",
            approve_receipt.transaction_hash
        );
    }

    // ── Step 2: Call mergePositions ────────────────────────────────────────
    // parentCollectionId = bytes32(0)
    let parent_collection_id = FixedBytes::<32>::ZERO;
    // partition = [1, 2] — standard binary partition
    let partition = vec![U256::from(1u64), U256::from(2u64)];

    tracing::info!(
        "Calling mergePositions: condition={} amount={} …",
        condition_id_str,
        amount
    );

    let merge_call = ctf.mergePositions(
        addresses.usdc,
        parent_collection_id,
        condition_id,
        partition,
        amount,
    );

    let pending_tx = merge_call
        .send()
        .await
        .context("mergePositions send failed")?;

    let receipt = pending_tx
        .get_receipt()
        .await
        .context("mergePositions receipt failed")?;

    let tx_hash = format!("{:#x}", receipt.transaction_hash);
    let gas_used = receipt.gas_used;

    tracing::info!("mergePositions confirmed: tx={} gas_used={}", tx_hash, gas_used);

    Ok(ExecutionResult::success(tx_hash, gas_used))
}

/// Grant setApprovalForAll for the CTF to spend your ERC-1155 tokens.
///
/// Idempotent — no-op if already approved.
///
/// # Arguments
/// * `rpc_url`  - Polygon JSON-RPC endpoint.
/// * `wallet`   - Funded Polygon wallet.
/// * `ctf`      - CTF contract address.
pub async fn ensure_approval(
    rpc_url: &str,
    wallet: EthereumWallet,
    ctf: Address,
) -> Result<ExecutionResult> {
    let provider = ProviderBuilder::new()
        .with_recommended_fillers()
        .wallet(wallet)
        .on_http(rpc_url.parse().context("Invalid RPC URL")?);

    let signer_address = provider
        .get_accounts()
        .await?
        .into_iter()
        .next()
        .context("No accounts")?;

    let erc1155 = IERC1155::new(ctf, &provider);

    let already = erc1155
        .isApprovedForAll(signer_address, ctf)
        .call()
        .await
        .map(|r| r._0)
        .unwrap_or(false);

    if already {
        tracing::info!("Already approved — nothing to do");
        return Ok(ExecutionResult::success("already_approved".into(), 0));
    }

    let tx = erc1155
        .setApprovalForAll(ctf, true)
        .send()
        .await
        .context("setApprovalForAll failed")?;

    let receipt = tx.get_receipt().await.context("receipt failed")?;
    let tx_hash = format!("{:#x}", receipt.transaction_hash);
    tracing::info!("Approval tx: {}", tx_hash);

    Ok(ExecutionResult::success(tx_hash, receipt.gas_used))
}
