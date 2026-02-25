# Smart Contract Development on Solana

<!--
RAG Metadata:
- Category: On-Chain Development
- Tags: anchor, smart-contract, solana-program, cpi, flash-loans, defi, ammp
- Related: 12-solana-bot-repos, 11-pacifica-integration, 14-bot-operations
-->

## Overview

This document covers developing on-chain trading bots and smart contracts on Solana using Anchor framework, including flash loans, arbitrage, and DEX integrations.

---

## Anchor Framework

**Documentation**: https://book.anchor-lang.com/

### Program Structure

```rust
use anchor_lang::prelude::*;
use anchor_spl::token::{Token, TokenAccount, Transfer};

declare_id!("YourProgramId11111111111111111111111111");

#[program]
pub mod trading_bot {
    use super::*;
    
    pub fn initialize_vault(ctx: Context<InitializeVault>) -> Result<()> {
        // Initialize trading vault
        Ok(())
    }
    
    pub fn execute_trade(
        ctx: Context<ExecuteTrade>,
        amount_in: u64,
        min_out: u64,
    ) -> Result<()> {
        // Execute trade via CPI to DEX
        Ok(())
    }
}

#[derive(Accounts)]
pub struct InitializeVault<'info> {
    #[account(
        init,
        payer = authority,
        space = 8 + Vault::INIT_SPACE,
        seeds = [b"vault", authority.key().as_ref()],
        bump
    )]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ExecuteTrade<'info> {
    #[account(mut)]
    pub vault: Account<'info, Vault>,
    #[account(mut)]
    pub user_token_account: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
}
```

### Key Anchor Features

- **Automatic account validation** with `#[account()]` constraints
- **Security macros**: `has_one`, `mut`, `close`, `realloc`
- **Auto-generated IDL** for client integration
- **Simplified CPI** with `CpiContext`

---

## Cross-Program Invocations (CPI)

### Jupiter CPI Integration

```rust
use jupiter_cpi;

pub fn swap_via_jupiter(
    ctx: Context<JupiterSwap>,
    route_plan: Vec<RoutePlan>,
    in_amount: u64,
    quoted_out_amount: u64,
    slippage_bps: u16,
) -> Result<()> {
    let signer_seeds: &[&[&[u8]]] = &[...];
    
    let accounts = jupiter_cpi::cpi::accounts::SharedAccountsRoute {
        token_program: ctx.accounts.token_program.to_account_info(),
        program_authority: ctx.accounts.program_authority.to_account_info(),
        user_transfer_authority: ctx.accounts.user_transfer_authority.to_account_info(),
        source_token_account: ctx.accounts.source.to_account_info(),
        destination_token_account: ctx.accounts.destination.to_account_info(),
    };
    
    let cpi_ctx = CpiContext::new_with_signer(
        ctx.accounts.jupiter.to_account_info(),
        accounts,
        signer_seeds,
    );
    
    jupiter_cpi::cpi::shared_accounts_route(
        cpi_ctx,
        route_id,
        route_plan,
        in_amount,
        quoted_out_amount,
        slippage_bps,
        platform_fee_bps,
    )
}
```

### Raydium CPI Integration

```rust
use raydium_amm;

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

---

## Flash Loans

### Kamino Finance Flash Loans

**Documentation**: https://kamino.com/docs/build/tutorials/borrow/flash-loans

```typescript
import { KaminoMarket, getFlashLoanInstructions } from '@kamino-finance/klend-sdk';

const usdcMint = address('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v');
const flashLoanAmount = new Decimal(1_000_000);

const { flashBorrowIx, flashRepayIx } = getFlashLoanInstructions({
    borrowIxIndex: 0,
    userTransferAuthority: signer,
    lendingMarketAuthority,
    lendingMarketAddress: market.getAddress(),
    reserve: usdcReserve,
    amountLamports: flashLoanAmount,
    destinationAta: usdcAta,
});
```

### Anchor Flash Loan Implementation

**Repository**: https://github.com/TengizSharafievWeb3/flashloan

```rust
#[program]
pub mod flash_loan {
    use super::*;
    
    pub fn borrow(ctx: Context<Loan>, borrow_amount: u64) -> Result<()> {
        // Transfer tokens to borrower
        let seeds = &[b"authority", &[bump]];
        let signer_seeds = &[&seeds[..]];
        
        let cpi_accounts = Transfer {
            from: ctx.accounts.vault.to_account_info(),
            to: ctx.accounts.borrower_ata.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        
        let cpi_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_accounts,
            signer_seeds,
        );
        
        transfer(cpi_ctx, borrow_amount)?;
        Ok(())
    }
    
    pub fn repay(ctx: Context<Loan>) -> Result<()> {
        // Verify repayment via instruction introspection
        let instruction_index = 0;
        let borrow_ix = load_instruction_at_checked(
            instruction_index, 
            &ctx.accounts.instruction_sysvar
        )?;
        // Validate borrow instruction exists and amounts match
        Ok(())
    }
}
```

---

## Token-2022 Program

**Documentation**: https://solana.com/docs/tokens/extensions

### Key Extensions

| Extension | Use Case |
|-----------|----------|
| Transfer Fees | Built-in fee collection on trades |
| Transfer Hook | Custom logic on token transfers |
| Metadata Pointer | On-chain token metadata |
| Interest-Bearing | Rebasing tokens for yield |
| Confidential Transfer | Private transactions |
| CPI Guard | Prevent unauthorized CPI calls |

### Transfer Hook Example

```rust
use spl_tlv_account_resolution::state::AccountDataResult;
use spl_transfer_hook_interface::instruction::TransferHookInstruction;

pub fn execute_transfer_hook(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    amount: u64,
) -> AccountDataResult<()> {
    // Custom logic: enforce trading restrictions, collect fees, etc.
    Ok(())
}
```

---

## Delta-Neutral Vaults

### Drift Protocol Integration

```rust
// Delta-neutral vault implementation
pub struct VaultState {
    pub total_deposits: u64,
    pub spot_position: u64,
    pub perp_hedge_position: i64,
    pub target_delta: i64, // 0 for delta-neutral
    pub last_rebalance: i64,
}

pub fn rebalance(ctx: Context<Rebalance>) -> Result<()> {
    let current_delta = calculate_current_delta(
        ctx.accounts.spot_position.amount,
        ctx.accounts.perp_position.base_amount,
    )?;
    
    if current_delta.abs() > DELTA_THRESHOLD {
        // Execute rebalancing via Drift CPI
        let hedge_amount = calculate_hedge_amount(current_delta);
        drift_cpi::perp_order(...)?;
    }
    
    Ok(())
}
```

---

## Automation

### TukTuk (Clockwork Replacement)

**Note**: Clockwork is no longer supported as of August 2023.

**Website**: https://www.tuktuk.fun/
**Repository**: https://github.com/helium/tuktuk

**Features**:
- Permissionless crank turner on Solana
- Cron-style scheduling
- Fully decentralized, on-chain automation
- Anyone can execute pending jobs and earn payment

```rust
use tuktuk_sdk::prelude::*;

let task_queue = TaskQueue::new()
    .with_cron_schedule("0 */5 * * * *") // Every 5 minutes
    .with_task(Task::new()
        .instruction(trade_rebalance_ix)
        .build())
    .build();

client.queue_task(task_queue).await?;
```

---

## Oracles

### Pyth Network

**Documentation**: https://docs.pyth.network/

**Solana Integration**:
```rust
// Cargo.toml
[dependencies]
pyth-solana-receiver-sdk = "x.y.z"

use pyth_solana_receiver_sdk::price_update::get_price_from_pyth;

pub fn get_current_price(
    ctx: Context<GetPrice>,
    price_feed_id: [u8; 32],
) -> Result<u64> {
    let price = get_price_from_pyth(
        &ctx.accounts.price_update,
        &price_feed_id,
        ctx.accounts.clock.unix_timestamp,
    )?;
    
    Ok(price.price)
}
```

### Switchboard

**Documentation**: https://docs.switchboard.xyz/

```rust
use switchboard_solana::AggregatorAccount;

pub fn get_price_from_switchboard(
    aggregator: &AccountInfo,
) -> Result<u64> {
    let aggregator = AggregatorAccount::new(aggregator)?;
    let result = aggregator.get_result()?;
    Ok(result.value as u64)
}
```

---

## NFT Trading

### Metaplex Core

**Documentation**: https://developers.metaplex.com/smart-contracts/core

**Key Features**:
- Single-account design (80%+ cost reduction)
- Enforced royalties
- Collection-level operations
- Flexible plugin system

### Metaboss (NFT CLI)

**Repository**: https://github.com/samuelvanderwaal/metaboss

```javascript
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults'
import { create } from '@metaplex-foundation/mpl-core'

const umi = createUmi('https://api.mainnet-beta.solana.com').use(mplCore())

const asset = await create(umi, {
    name: 'Trading Bot NFT',
    uri: 'https://example.com/metadata.json',
}).sendAndConfirm(umi)
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ON-CHAIN TRADING BOT ARCHITECTURE                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐            │
│  │  Off-Chain  │    │  On-Chain   │    │ Infrastructure│            │
│  │   Layer     │    │   Layer     │    │    Layer     │            │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘            │
│         │                  │                  │                    │
│         ▼                  ▼                  ▼                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐            │
│  │ - Monitor   │    │ - Anchor    │    │ - Pyth      │            │
│  │ - Signal    │───▶│   Programs  │◀──▶│ - Switchboard│            │
│  │   Generator │    │ - CPI Calls │    │ - TukTuk    │            │
│  │ - Risk Mgmt │    │ - Flash     │    │ - Jupiter   │            │
│  └─────────────┘    │   Loans     │    │ - Kamino    │            │
│                     └──────┬──────┘    └─────────────┘            │
│                            │                                       │
│                            ▼                                       │
│                     ┌─────────────┐                               │
│                     │  DEX Layer  │                               │
│                     │ - Raydium   │                               │
│                     │ - Orca      │                               │
│                     │ - Meteora   │                               │
│                     │ - Drift     │                               │
│                     │ - Jupiter   │                               │
│                     └─────────────┘                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Resources

| Category | Resource | URL |
|----------|----------|-----|
| **Framework** | Anchor Book | https://book.anchor-lang.com/ |
| **DEX Integration** | Jupiter CPI | https://dev.jup.ag/docs/old/apis/cpi |
| **Flash Loans** | Kamino Docs | https://kamino.com/docs/build |
| **Perpetuals** | Drift Protocol | https://github.com/drift-labs/protocol-v2 |
| **Oracles** | Pyth Network | https://docs.pyth.network/ |
| **Automation** | TukTuk | https://github.com/helium/tuktuk |
| **Token-2022** | SPL Extensions | https://solana.com/docs/tokens/extensions |
| **NFTs** | Metaplex Core | https://developers.metaplex.com/ |

---

## Related Reports

- [12-solana-bot-repos.md](./12-solana-bot-repos.md) - External bot repositories
- [11-pacifica-integration.md](./11-pacifica-integration.md) - Pacifica exchange
- [14-bot-operations.md](./14-bot-operations.md) - Production deployment
