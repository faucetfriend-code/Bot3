# EVM Trading Bot Development

<!--
RAG Metadata:
- Category: EVM Development
- tags: ethereum, defi, solidity, web3, ethers, uniswap, aave, gmx, Arbitrum, Optimism, polygon, avalanche, flash-loans, mev
- Related: 11-pacifica-integration, 12-solana-bot-repos, 14-bot-operations, 16-bitcoin-development
-->

## Overview

This document covers trading bot development on EVM (Ethereum Virtual Machine) chains including Ethereum mainnet, Layer 2s (Arbitrum, Optimism, Base), and other EVM-compatible networks.

---

## EVM Chains & RPC

### Major EVM Chains

| Chain | Chain ID | RPC URL | Documentation |
|-------|----------|---------|---------------|
| Ethereum | 1 | `https://eth.llamarpc.com` | ethereum.org |
| Arbitrum | 42161 | `https://arb1.arbitrum.io/rpc` | docs.arbitrum.io |
| Optimism | 10 | `https://mainnet.optimism.io` | docs.optimism.io |
| Base | 8453 | `https://mainnet.base.org` | docs.base.org |
| Polygon | 137 | `https://polygon-rpc.com` | polygon.technology |
| Avalanche C-Chain | 43114 | `https://api.avax.network/ext/bc/C/rpc` | docs.avax.network |

### RPC Providers

| Provider | Free Tier | Features |
|----------|-----------|----------|
| **Infura** | 100k/day | WebSocket, IPFS |
| **Alchemy** | Unlimited | Build tools, Alerts |
| **QuickNode** | 50k/day | Multi-chain (60+) |

---

## Trading Libraries

### Python Libraries

**web3.py**
```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider('https://mainnet.infura.io/v3/YOUR_KEY'))

# Get latest block
block = w3.eth.get_block('latest')

# Send transaction
w3.eth.send_transaction({
    'from': '0x...',
    'to': '0x...',
    'value': Web3.to_wei(1, 'ether')
})
```

**web3-ethereum-defi**
- Documentation: https://web3-ethereum-defi.readthedocs.io/
- Features: DeFi primitives, Uniswap, Aave, Safe integrations

### TypeScript/JavaScript Libraries

**ethers.js**
```typescript
import { ethers } from 'ethers'

const provider = new ethers.JsonRpcProvider('https://mainnet.infura.io/v3/YOUR_KEY')
const wallet = new ethers.Wallet(privateKey, provider)

const tx = await wallet.sendTransaction({
  to: '0x...',
  value: ethers.parseEther('1.0')
})
```

**viem** (Modern, Type-Safe)
```typescript
import { createPublicClient, http } from 'viem'
import { mainnet } from 'viem/chains'

const client = createPublicClient({
  chain: mainnet,
  transport: http('https://mainnet.infura.io/v3/YOUR_KEY')
})
```

### Universal Trading Library

**CCXT** - 100+ exchange integrations
```python
import ccxt

binance = ccxt.binance({'apiKey': 'KEY', 'secret': 'SECRET'})
order = binance.create_order('BTC/USDT', 'market', 'buy', 0.01)
```

---

## DEX Aggregators

### 1inch
- Documentation: https://portal.1inch.dev/documentation
- Python: `pip install 1inch.py`

```python
from one_inch import OneInch

one_inch = OneInch("YOUR_API_KEY")
swap_data = one_inch.get_swap(
    from_token="0x...",
    to_token="0x...",
    amount="1000000000000000000",
    from_address="0x..."
)
```

### Uniswap

**Uniswap V3 SDK**
```typescript
import { AlphaRouter } from '@uniswap/smart-order-router'

const router = new AlphaRouter({ chainId: 1, provider })
const route = await router.route(
  currencyAmount,
  tokenOut,
  TradeType.EXACT_INPUT,
  { recipient }
)
```

### Paraswap
- API: https://api.paraswap.io/v5/
- Features: Augustus routing, multi-chain

---

## Lending Protocols

### Aave V3
- Documentation: https://docs.aave.com/developers/

```python
from aave import AaveV3

aave = AaveV3(provider_url, pool_address)
# Supply collateral
aave.supply(asset, amount, on_behalf_of)
# Borrow
aave.borrow(asset, amount, interest_rate_mode, on_behave_of)
```

### Compound
- Documentation: https://docs.compound.finance/

---

## Perpetual DEX

### GMX
- Documentation: https://gmx-docs.io/docs/intro/
- Features: Up to 100x leverage, Chainlink oracles
- Chains: Arbitrum, Avalanche

### dYdX
- Documentation: https://docs.dydx.xyz/
- Features: 200+ perpetual markets, up to 100x leverage

### Vertex Protocol
- Documentation: https://docs.vertexprotocol.com/
- Chains: Arbitrum, Blast, Mantle, Base

---

## Smart Contracts

### Flash Loan Example (Aave)

```solidity
import { FlashLoanSimpleReceiverBase } from "@aave/core-v3/contracts/flashloan/base/FlashLoanSimpleReceiverBase.sol";
import { IPool } from "@aave/core-v3/contracts/interfaces/IPool.sol";

contract FlashLoanArbitrage is FlashLoanSimpleReceiverBase {
    constructor(IPoolAddressesProvider provider) FlashLoanSimpleReceiverBase(provider) {}
    
    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        // Your arbitrage logic here
        uint256 amountToRepay = amounts[0] + premiums[0];
        IERC20(assets[0]).approve(address(POOL), amountToRepay);
        return true;
    }
    
    function initiateFlashLoan() public {
        address[] memory assets = new address[](1);
        assets[0] = tokenAddress;
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = loanAmount;
        POOL.flashLoanSimple(address(this), assets[0], amounts[0], bytes(""), 0);
    }
}
```

### Gas Optimization

```solidity
contract GasOptimized {
    // Pack variables - smaller types first
    uint128 public a;
    uint128 public b;
    uint256 public c;
    
    // Use calldata for read-only arrays
    function processData(uint256[] calldata data) external {
        uint256 len = data.length;
        for (uint256 i = 0; i < len;) {
            unchecked { ++i; }
        }
    }
    
    // Custom errors (cheaper than strings)
    error InsufficientBalance(uint256 requested, uint256 available);
}
```

---

## MEV Strategies

### Flashbots

**MEV Types**:
1. **Front-running**: Detect pending trades, place orders ahead
2. **Back-running**: Place orders after large trades
3. **Sandwich attacks**: Front + back run combined

**Flashbots Protect**:
```python
# Flashbots RPC: rpc.flashbots.net/fast
from flashbots import flashbot

flashbot.sign_bundle(w3, [signed_tx])
flashbot.send_bundle(bundle, target_block_number)
```

### MEV Resources

| Resource | URL |
|----------|-----|
| Flashbots Docs | https://docs.flashbots.net/ |
| MEV-Boost | https://github.com/flashbots/mev-boost |

---

## Code Repositories

### Arbitrage Bots

| Repository | Stars | Description |
|-----------|-------|-------------|
| [dex-arbitrage-bot](https://github.com/vj013il/dex-arbitrage-bot) | 185 | Multi-DEX (Uniswap, Sushi, Balancer, Curve) |
| [mev-bot](https://github.com/dream-423/mev-bot) | 6 | Flashloan arbitrage templates |
| [EVM-Arbitrage-Bot](https://github.com/g0drlc/evm-arbitrage-bot) | - | Cross-chain arbitrage |

### Liquidation Bots

| Repository | Stars | Description |
|-----------|-------|-------------|
| [aave-liquidation](https://github.com/lbkolev/aave-liquidation) | 20 | Aave V3 liquidation |
| [aave-v2-liquidator](https://github.com/ashutoshvarma/aave-v2-liquidator) | - | Fast Aave V2 liquidator |

### Grid Trading Bots

| Repository | Stars | Description |
|-----------|-------|-------------|
| [infinity-grid](https://github.com/btschwertfeger/infinity-grid) | 13 | Multi-exchange grid trading |
| [AS-Grid](https://github.com/princeniu/AS-Grid) | 35 | Multi-exchange futures grid |

---

## Smart Contract Wallets

### Safe (Gnosis)
```python
from gnosis.safe import Safe

safe = Safe(address='0x...', eth_client=w3)
tx = safe.build_transaction(to='0x...', value=1e18, data=b'')
safe.sign_transaction(tx)
safe.execute_transaction(tx)
```

**Python SDK**: `pip install safe-eth-py`

### Argent
- Documentation: https://docs.argent.xyz/
- Features: Social recovery, session keys, multisig

---

## Development Frameworks

| Framework | URL | Language |
|-----------|-----|----------|
| **Foundry** | https://book.getfoundry.sh/ | Solidity/Rust |
| **Hardhat** | https://hardhat.org/ | JavaScript/TypeScript |
| **Ape** | https://docs.apeworx.io/ | Python |

---

## Essential Libraries

### Python
```bash
pip install web3 safe-eth-py 1inch.py ccxt eth-account aiohttp
```

### TypeScript
```bash
npm install ethers viem @uniswap/sdk-core @aave/core-v3
```

---

## Related Reports

- [11-pacifica-integration.md](./11-pacifica-integration.md) - Pacifica exchange
- [12-solana-bot-repos.md](./12-solana-bot-repos.md) - Solana bots
- [14-bot-operations.md](./14-bot-operations.md) - Production deployment
- [16-bitcoin-development.md](./16-bitcoin-development.md) - Bitcoin bots
