# BrickLink server for Claude Desktop

Lets Claude read your BrickLink store through the official BrickLink Store API: look up catalog items, part out sets, check price guides, list and view your store lots, and view your orders. Changing lots (price, quantity, stockroom, description, new lots) is **off by default**. There is no delete tool and no order-status or feedback tool.

Your API keys stay on your own computer, in Claude Desktop's config file. Never paste them into a chat, a Discord message, a screenshot, or a GitHub issue.

Not affiliated with or endorsed by BrickLink or the LEGO Group. Each user registers their own BrickLink API keys and is responsible for following BrickLink's API Terms of Use. Released under the MIT License (see `LICENSE`).

## What you need

- A BrickLink **seller** account (the API is only offered to sellers)
- Claude Desktop on Windows or Mac
- The file `bricklink_mcp.py`

## 1. Get your BrickLink API keys

1. Signed in to BrickLink, open https://www.bricklink.com/v2/api/register_consumer.page
2. Enter the IP address shown at the top of that page, keep the mask at `255.255.255.255`, leave the callback URL alone, agree to the terms, and click Register. This gives you a **Consumer Key** and **Consumer Secret**.
3. Add an access token for the same IP. This gives you a **Token Value** and **Token Secret**.

Notes:
- If your home IP changes, calls fail with an IP-mismatch error; update the token's IP on that page. Setting both IP and mask to `0.0.0.0` allows any IP but means the four keys alone can control your store.
- If you ever click **Renew Consumer**, you must also create a new access token. Old token values stop working and you'll see "Invalid Signature".

## 2. Install uv (runs the Python script and its dependencies)

Windows (PowerShell):

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Mac:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 3. Save the script

Windows: `C:\Users\<you>\bricklink-mcp\bricklink_mcp.py`
Mac: `~/bricklink-mcp/bricklink_mcp.py`

## 4. Test your keys (optional but recommended)

Windows PowerShell:

```
$env:BRICKLINK_CONSUMER_KEY="..."; $env:BRICKLINK_CONSUMER_SECRET="..."
$env:BRICKLINK_TOKEN_VALUE="..."; $env:BRICKLINK_TOKEN_SECRET="..."
& "$env:USERPROFILE\.local\bin\uv.exe" run "$env:USERPROFILE\bricklink-mcp\bricklink_mcp.py" --check
```

You want a line starting `OK: connected to BrickLink`. Close the window afterwards so the keys aren't left on screen.

## 5. Add it to Claude Desktop

Open Claude Desktop → Settings → Developer → **Edit config**. Right after the first `{` in the file, paste this (replace `<you>` and the four values, keep the quotes and the final comma):

```json
  "mcpServers": {
    "bricklink": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": ["run", "C:\\Users\\<you>\\bricklink-mcp\\bricklink_mcp.py"],
      "env": {
        "BRICKLINK_CONSUMER_KEY": "...",
        "BRICKLINK_CONSUMER_SECRET": "...",
        "BRICKLINK_TOKEN_VALUE": "...",
        "BRICKLINK_TOKEN_SECRET": "...",
        "BRICKLINK_ALLOW_WRITES": "false"
      }
    }
  },
```

- If the file already has an `"mcpServers"` section, put the `"bricklink": {...}` entry inside it instead of adding a second one.
- Mac paths look like `/Users/<you>/.local/bin/uv` and `/Users/<you>/bricklink-mcp/bricklink_mcp.py` (single slashes).
- The keys must go in this file; Claude Desktop does not pass ordinary system environment variables to these servers.

## 6. Restart Claude Desktop properly

Closing the window does **not** quit the app. On Windows run `Stop-Process -Name claude -Force` in PowerShell (or right-click the tray icon → Quit), then reopen it. On Mac, Claude → Quit.

Then Settings → Developer should show **bricklink — Running**.

## Using it

Start a new chat and ask in plain English, for example:

- "Find my Pirates of Barracuda Bay lot in BrickLink and show its price and status."
- "What has 21322 sold for used in the last 6 months?"
- "Show me my open BrickLink orders."
- "Part out 75292-1 and tell me which pieces are worth the most."

To let Claude change listings, set `"BRICKLINK_ALLOW_WRITES": "true"` and restart the app again. Claude Desktop still asks you to approve each change.

## Troubleshooting

- **Nothing shows under Developer after editing the config (Windows Store version):** the app may read `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`. The live log (`%LOCALAPPDATA%\Claude\Logs\main.log`) has a line "Reading claude_desktop_config.json from ..." that shows which file it uses.
- **Config error "Expected ',' or '}'":** a comma is missing between the `mcpServers` block and whatever follows it.
- **"Invalid Signature":** one of the four keys is wrong, or you renewed the consumer without creating a new token.
- **"No module named mcp.server.fastmcp":** you have an old copy of the script; the current one pins `mcp<2`.
