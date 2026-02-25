# Solana Trading Bot Repositories

<!--
RAG Metadata:
- Category: External Resources
- Tags: solana, github, trading-bots, arbitrage, jupiter, raydium, orca, meteora, mev, dex
- Related: 11-pacifica-integration, 13-smart-contract-development, 14-bot-operations
-->

## Overview

This document catalogs popular GitHub repositories and resources for building Solana-based trading bots, including DEX aggregators, arbitrage bots, and MEV strategies.

---

## DEX Aggregator Integration

### Jupiter

**Documentation**: https://station.jup.ag/docs/price-api
**Ultra Swap API**: https://dev.jup.ag/docs/ultra

Features:
- RPC-less architecture
- Gasless trading
- Automatic slippage optimization
- Sub-second transaction landing

**Python Integration**:
```python
import requests

# Get quote
response = requests.get('https://quote-api.jup.ag/v6/quote', params={
    'inputMint': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC
    'outputMint': 'So11111111111111111111111111111111111111112',  # SOL
    'amount': '1000000',  # 1 USDC in lamports
    'slippageBps': 50
})
quote = response.json()
```

### Raydium

**Repository**: https://github.com/raydium-io/raydium-cpi-example
**Stars**: 124+

**Raydium CPI Swap Example**:
```rust
use anchor_lang::prelude::*;

pub fn swap_via_raydium(
    ctx: Context<RaydiumSwap>,
    amount_in: u64,
    minimum_out: u64,
) -> Result<()> {
    let cpi_accounts = raydium_amm::cpi::accounts::Swap {
        token_program: ctx.accounts.token_program.to_account_info(),
        amm: ctx.accounts.amm.to_account_info(),
        authority: ctx.accounts.authority.to_account_info(),
        user_source: ctx.accounts.user_source.to_account_info(),
        pool_coin: ctx.accounts.pool_coin.to_account_info(),
        pool_pc: ctx.accounts.pool_pc.to_account_info(),
        user_dest: ctx.accounts.user_dest.to_account_info(),
    };
    
    let cpi_ctx = CpiContext::new(
        ctx.accounts.raydium_program.to_account_info(),
        cpi_accounts,
    );
    
    raydium_amm::cpi::swap(cpi_ctx, amount_in, minimum_out)
}
```

### Meteora

**Repository**: https://github.com/MeteoraAg/cpi-examples
**Stars**: 84+

Features:
- DLMM (Dynamic Liquidity Market Maker) integration
- Vault systems
- Multi-hop swaps

---

## Arbitrage Bots

### Multi-DEX Arbitrage Bot

**Repository**: https://github.com/OnlyForward0613/Solana-Arbitrage-Bot
**Stars**: 285+ | **Forks**: 118+

**Architecture**:
```
graph TD
    A[Price Monitor] --> B[Opportunity Detector]
    B --> C{Strategy Selector}
    C --> D[Two-Hop Strategy]
    C --> E[Triangle Strategy]
    C --> F[Multi-DEX Strategy]
    D --> G[Execution Engine]
    E --> G
    F --> G
```

**Key Program IDs**:
```rust
pub const RAYDIUM_PROGRAM_ID: &str = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8";
pub const ORCA_WHIRLPOOL_PROGRAM_ID: &str = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc";
pub const METEORA_PROGRAM_ID: &str = "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K";
```

### High-Performance MEV Bot

**Repository**: https://github.com/solrusty1210/solana-mev-bot

Features:
- Flash-loan-integrated MEV arbitrage
- Kamino flash loans
- Atomic multi-leg trades
- Jito MEV integration

### Flash Loan Arbitrage

**Repository**: https://github.com/benjaminTan10/Solana-Flash-Loan-Arbitrage-Bot
**Stars**: 171+

Features:
- Kamino Finance integration
- Jupiter aggregation
- Multi-leg arbitrage detection

---

## Liquidation Bots

### Solend Liquidator

**Repository**: https://github.com/solendprotocol/liquidator

Open-source liquidation bot for Solend protocol with production-ready implementation.

**Architecture**:
```rust
pub struct LiquidationOpportunity {
    pub obligation: Pubkey,
    pub collateral_mint: Pubkey,
    pub borrowed_mint: Pubkey,
    pub collateral_amount: u64,
    pub borrowed_amount: u64,
    pub health_factor: u64,
    pub profit_estimate: u64,
}

pub fn find_liquidation_opportunities(
    market: &KaminoMarket,
    price_oracle: &dyn PriceOracle,
) -> Vec<LiquidationOpportunity> {
    // Scan all obligations for under-collateralized positions
    // Calculate profit after liquidation bonus and gas costs
}
```

### Blend Capital Liquidator

**Repository**: https://github.com/blend-capital/liquidation-bot

---

## Yield Farming Automation

### Solana Yield Farmer

**Repository**: https://github.com/machenxi/solana-yield-farmer
**Stars**: 98+

Features:
- Tracks Meteora, Raydium, Orca vaults
- Automatically rotates capital to highest-yield pools
- Multi-account support

### Meteora Batch Claimer

**Repository**: https://github.com/meteora-claimer/meteora-batch-claimer-bot

Features:
- Auto-claim Meteora rewards
- Bulk claiming for efficiency
- Scheduled execution

---

## Perpetual DEX Integration

### Drift Protocol v2

**Repository**: https://github.com/drift-labs/protocol-v2
**Stars**: 272+ | **Forks**: 141+

On-chain perpetuals DEX with multiple liquidity mechanisms.

**Integration Example**:
```typescript
import { DriftClient, PerpMarkets, getMarketOrderParams } from '@drift-labs/sdk';

// Initialize Drift client
const driftClient = new DriftClient({
    connection,
    wallet,
    programID: new PublicKey('dRiftyHA39MWEi3J9JQZ1vChz5tH8cP2z9qQz9p9y9R'),
});

// Place perpetual order
const orderParams = getMarketOrderParams({
    marketIndex: 0, // SOL-PERP
    direction: PositionDirection.LONG,
    amount: new BN(1_000_000),
    price: new BN(100_000_000),
});

await driftClient.placePerpOrder(orderParams);
```

### Kamino KLend

**Repository**: https://github.com/Kamino-Finance/klend
**Stars**: 159+

Open-source lending program with flash loan support.

---

## Key Libraries

### @solana/web3.js

```javascript
import { Connection, PublicKey, Keypair } from '@solana/web3.js';

const connection = new Connection('https://api.mainnet-beta.solana.com');
const wallet = Keypair.fromSecretKey(secretKey);

// Get token balance
const balance = await connection.getTokenAccountBalance(tokenAccountPubkey);
```

### Anchor Framework

**Book**: https://book.anchor-lang.com/

```rust
use anchor_lang::prelude::*;

declare_id!("YourProgramId11111111111111111111111111");

#[program]
pub mod trading_bot {
    use super::*;
    
    pub fn initialize_vault(ctx: Context<InitializeVault>) -> Result<()> {
        // Initialize trading vault
        Ok(())
    }
}
```

### SPL Token

**Documentation**: https://spl.solana.com/token

```rust
use anchor_spl::token::{self, Token, TokenAccount, Transfer};

pub fn transfer_tokens(
    ctx: Context<TransferTokens>,
    amount: u64,
) -> Result<()> {
    let cpi_accounts = Transfer {
        from: ctx.accounts.from.to_account_info(),
        to: ctx.accounts.to.to_account_info(),
        authority: ctx.accounts.authority.to_account_info(),
    };
    
    let cpi_ctx = CpiContext::new(
        ctx.accounts.token_program.to_account_info(),
        cpi_accounts,
    );
    
    token::transfer(cpi_ctx, amount)
}
```

---

## RPC Providers

| Provider | Features | URL |
|----------|----------|-----|
| **Helius** | Free tier, WebSocket, historical | https://helius.xyz |
| **QuickNode** | Enterprise, dedicated endpoints | https://quicknode.com |
| **Triton** | Low latency, priority support | https://triton.one |
| **Alchemy** | Multi-chain, analytics | https://alchemy.com |

---

## Repository Summary

| Repository | Stars | Purpose |
|------------|-------|---------|
| [Solana-Arbitrage-Bot](https://github.com/OnlyForward0613/Solana-Arbitrage-Bot) | 285+ | Multi-DEX arbitrage |
| [Solana-Flash-Loan-Arbitrage-Bot](https://github.com/benjaminTan10/Solana-Flash-Loan-Arbitrage-Bot) | 171+ | Kamino + Jupiter |
| [multidex-solana-arbitragebot-jito-darkpool](https://github.com/vj013il/multidex-solana-arbitragebot-jito-darkpool) | 181+ | Multi-DEX + dark pool |
| [protocol-v2](https://github.com/drift-labs/protocol-v2) | 272+ | Drift perpetuals |
| [klend](https://github.com/Kamino-Finance/klend) | 159+ | Kamino lending |
| [raydium-cpi-example](https://github.com/raydium-io/raydium-cpi-example) | 124+ | Raydium integration |
| [cpi-examples](https://github.com/MeteoraAg/cpi-examples) | 84+ | Meteora integration |
| [solana-yield-farmer](https://github.com/machenxi/solana-yield-farmer) | 98+ | Yield automation |

---

## Related Reports

- [11-pacifica-integration.md](./11-pacifica-integration.md) - Pacifica exchange
- [13-smart-contract-development.md](./13-smart-contract-development.md) - On-chain development
- [14-bot-operations.md](./14-bot-operations.md) - Production deployment
