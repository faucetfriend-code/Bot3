import json
import os
from core_logic.pacifica_client import PacificaClient, PacificaEnvironment
from dotenv import load_dotenv

load_dotenv()

private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

client = PacificaClient(private_key, public_key, PacificaEnvironment.TESTNET)

print("Testing ticker for SUI:")
ticker_sui = client.get_ticker("SUI")
print(json.dumps(ticker_sui, indent=2))

print("\n\nTesting ticker for BTC:")
ticker_btc = client.get_ticker("BTC")
print(json.dumps(ticker_btc, indent=2))
