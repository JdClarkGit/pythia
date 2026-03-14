//! Solidity ABI bindings generated with alloy sol! macro.

use alloy::sol;

// ─── Gnosis ConditionalTokens Framework ─────────────────────────────────────

sol!(
    #[allow(missing_docs)]
    #[sol(rpc)]
    ConditionalTokens,
    "abi/ctf.json"
);

// ─── Polymarket CTF Exchange ─────────────────────────────────────────────────

sol!(
    #[allow(missing_docs)]
    #[sol(rpc)]
    CTFExchange,
    "abi/ctf_exchange.json"
);

// ─── Minimal ERC-20 (USDC) ──────────────────────────────────────────────────

sol! {
    #[allow(missing_docs)]
    #[sol(rpc)]
    interface IERC20 {
        function approve(address spender, uint256 amount) external returns (bool);
        function allowance(address owner, address spender) external view returns (uint256);
        function balanceOf(address account) external view returns (uint256);
    }
}

// ─── Minimal ERC-1155 ───────────────────────────────────────────────────────

sol! {
    #[allow(missing_docs)]
    #[sol(rpc)]
    interface IERC1155 {
        function balanceOf(address account, uint256 id) external view returns (uint256);
        function setApprovalForAll(address operator, bool approved) external;
        function isApprovedForAll(address account, address operator) external view returns (bool);
    }
}
