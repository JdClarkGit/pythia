//! chain_executor — CLI entry point for on-chain merge arbitrage operations.
//!
//! # Sub-commands
//!
//! ```text
//! chain_executor merge    --condition-id <HEX> --amount <UNITS> [--rpc <URL>] [--ctf <ADDR>] [--usdc <ADDR>]
//! chain_executor approve  --ctf <ADDR> [--rpc <URL>]
//! chain_executor balance  --wallet <ADDR> [--rpc <URL>]
//! ```
//!
//! All outputs are JSON on stdout so the Python caller can parse them easily.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use alloy::primitives::U256;
use tracing_subscriber::EnvFilter;

use chain_executor::{
    merge::{ensure_approval, execute_merge},
    types::{Addresses, ExecutionResult},
    wallet::load_wallet_from_env,
};

// ─── CLI definition ──────────────────────────────────────────────────────────

#[derive(Parser, Debug)]
#[command(
    name = "chain_executor",
    about = "Polymarket merge-arb on-chain executor",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Call mergePositions() on the CTF to collect $1 USDC per share-pair.
    Merge {
        /// 32-byte condition ID (hex, with or without 0x prefix).
        #[arg(long, env = "CONDITION_ID")]
        condition_id: String,

        /// Amount in 6-decimal token units (e.g. 1000000 = 1.0 shares).
        #[arg(long, env = "AMOUNT")]
        amount: u64,

        /// Polygon JSON-RPC URL.
        #[arg(long, env = "POLYGON_RPC_URL", default_value = "https://polygon-rpc.com")]
        rpc: String,

        /// ConditionalTokens contract address.
        #[arg(long, env = "CTF_ADDRESS")]
        ctf: Option<String>,

        /// USDC contract address.
        #[arg(long, env = "USDC_ADDRESS")]
        usdc: Option<String>,

        /// Gas buffer multiplier (e.g. 1.2 = 20 % extra gas).
        #[arg(long, default_value = "1.2")]
        gas_multiplier: f64,
    },

    /// Grant setApprovalForAll so the CTF can spend your ERC-1155 tokens.
    Approve {
        /// CTF contract address to approve.
        #[arg(long, env = "CTF_ADDRESS")]
        ctf: Option<String>,

        /// Polygon JSON-RPC URL.
        #[arg(long, env = "POLYGON_RPC_URL", default_value = "https://polygon-rpc.com")]
        rpc: String,
    },

    /// Query USDC balance of a wallet.
    Balance {
        /// Wallet address to query.
        #[arg(long, env = "WALLET_ADDRESS")]
        wallet: String,

        /// USDC contract address.
        #[arg(long, env = "USDC_ADDRESS")]
        usdc: Option<String>,

        /// Polygon JSON-RPC URL.
        #[arg(long, env = "POLYGON_RPC_URL", default_value = "https://polygon-rpc.com")]
        rpc: String,
    },
}

// ─── Entry point ─────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    // Load .env if present (non-fatal if missing)
    let _ = dotenvy::dotenv();

    // Tracing subscriber — level controlled by RUST_LOG env var
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();

    let cli = Cli::parse();

    let result = run(cli).await;

    match result {
        Ok(exec_result) => {
            exec_result.print();
            std::process::exit(0);
        }
        Err(e) => {
            ExecutionResult::failure(format!("{e:#}")).print();
            std::process::exit(1);
        }
    }
}

async fn run(cli: Cli) -> Result<ExecutionResult> {
    match cli.command {
        Commands::Merge {
            condition_id,
            amount,
            rpc,
            ctf,
            usdc,
            gas_multiplier,
        } => {
            let wallet = load_wallet_from_env().context("Load wallet")?;
            let addresses = Addresses::from_env(ctf.as_deref(), usdc.as_deref())
                .context("Parse addresses")?;
            execute_merge(
                &rpc,
                wallet,
                &addresses,
                &condition_id,
                U256::from(amount),
                gas_multiplier,
            )
            .await
            .context("execute_merge")
        }

        Commands::Approve { ctf, rpc } => {
            let wallet = load_wallet_from_env().context("Load wallet")?;
            let addresses = Addresses::from_env(ctf.as_deref(), None)
                .context("Parse addresses")?;
            ensure_approval(&rpc, wallet, addresses.ctf)
                .await
                .context("ensure_approval")
        }

        Commands::Balance { wallet, usdc, rpc } => {
            query_balance(&rpc, &wallet, usdc.as_deref()).await
        }
    }
}

/// Query the USDC balance of a wallet and print as JSON.
async fn query_balance(rpc: &str, wallet_addr: &str, usdc_override: Option<&str>) -> Result<ExecutionResult> {
    use alloy::providers::{Provider, ProviderBuilder};
    use chain_executor::contracts::IERC20;

    let addresses = Addresses::from_env(None, usdc_override)?;
    let provider = ProviderBuilder::new()
        .on_http(rpc.parse().context("Invalid RPC URL")?);

    let addr: alloy::primitives::Address = wallet_addr
        .parse()
        .context("Invalid wallet address")?;

    let usdc = IERC20::new(addresses.usdc, &provider);
    let balance = usdc
        .balanceOf(addr)
        .call()
        .await
        .map(|r| r._0)
        .context("balanceOf failed")?;

    // USDC has 6 decimals
    let balance_human = balance.to::<u128>() as f64 / 1_000_000.0;
    tracing::info!("USDC balance: {:.6}", balance_human);

    Ok(ExecutionResult::success(format!("{:.6}", balance_human), 0))
}
