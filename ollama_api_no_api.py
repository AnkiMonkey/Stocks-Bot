"""
Volatility Alert Bot - S&P 500 edition
----------------------------------------
Checks the entire S&P 500 and finds stocks down more than the
configured threshold.

For each qualifying stock it collects:

    MARKET
    - Current price
    - Previous close
    - Today's % move
    - 1-week % move
    - 1-month % move
    - 1-year % move
    - Trading volume
    - Average volume
    - Relative volume

    FUNDAMENTALS
    - Market cap
    - P/E
    - Forward P/E
    - Revenue growth
    - Earnings growth
    - Profit margin
    - Operating margin
    - Debt / equity
    - Free cash flow

    NEWS
    - Recent Google News RSS headlines/snippets

Gemma 3:4b then evaluates:

    BUY THE DIP
    CONSIDER
    AVOID

Email format:

    Company    Adobe Inc.
    Ticker     ADBE
    Change     -6.87%
    Price      1045.82
    Verdict    CONSIDER
    Reason     ...
    Risk       MEDIUM
    Chart      Google Finance link

Dependencies:
    pip install yfinance pandas --break-system-packages

Ollama:
    gemma3:4b

Run:
    python ollama_api.py
"""

import csv
import html
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText

import yfinance as yf

# API data here (DO NOT SHARE PUBLICLY)
import config


# ============================================================
# OLLAMA
# ============================================================

OLLAMA_MODEL = "gemma3:4b"
OLLAMA_URL = "http://localhost:11434/api/generate"

COMPANY_NAMES: dict[str, str] = {}


# ============================================================
# S&P 500
# ============================================================

def load_sp500_tickers() -> list[str]:
    """Load S&P 500 tickers from local CSV."""

    if not os.path.exists(config.SP500_CSV_PATH):
        raise FileNotFoundError(
            f"Could not find {config.SP500_CSV_PATH}. "
            f"Run: python update_sp500_list.py"
        )

    tickers = []

    with open(
        config.SP500_CSV_PATH,
        encoding="utf-8",
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            symbol = row["Symbol"].strip()
            symbol = symbol.replace(".", "-")

            if symbol not in config.EXCLUDE_TICKERS:

                COMPANY_NAMES[symbol] = (
                    row.get("Security", "").strip()
                )

                tickers.append(symbol)

    return tickers


# ============================================================
# MARKET DATA
# ============================================================

def download_sp500_data(tickers: list[str]):
    """Download intraday data for the entire S&P 500."""

    print(
        f"Downloading data for {len(tickers)} "
        f"S&P 500 companies..."
    )

    data = yf.download(
        tickers=tickers,
        period=config.YFINANCE_PERIOD,
        interval=config.YFINANCE_INTERVAL,
        group_by="ticker",
        prepost=config.INCLUDE_PREPOST,
        threads=True,
        progress=False,
    )

    return data


def get_intraday_drop_percent(
    data,
    ticker: str,
) -> tuple[float, float, float]:

    try:

        closes = data[ticker]["Close"].dropna()

    except KeyError:

        raise ValueError(
            f"No downloaded data for {ticker}"
        )

    if len(closes) < 2:

        raise ValueError(
            f"Not enough data for {ticker}"
        )

    latest_date = closes.index[-1].date()

    previous_session_closes = closes[
        closes.index.date < latest_date
    ]

    if previous_session_closes.empty:

        raise ValueError(
            f"No previous session close for {ticker}"
        )

    previous_close = previous_session_closes.iloc[-1]
    current_price = closes.iloc[-1]

    percent_change = (
        (current_price - previous_close)
        / previous_close
    ) * 100

    return (
        percent_change,
        previous_close,
        current_price,
    )


# ============================================================
# PRICE HISTORY
# ============================================================

def get_price_context(
    ticker: str,
) -> dict[str, object]:

    result = {
        "week_change": None,
        "month_change": None,
        "year_change": None,
        "volume": None,
        "average_volume": None,
        "relative_volume": None,
    }

    try:

        stock = yf.Ticker(ticker)

        history = stock.history(
            period="1y",
            interval="1d",
        )

        if history.empty:
            return result

        closes = history["Close"].dropna()
        volumes = history["Volume"].dropna()

        if len(closes) >= 5:

            week_change = (
                (
                    closes.iloc[-1]
                    / closes.iloc[-5]
                ) - 1
            ) * 100

            result["week_change"] = week_change

        if len(closes) >= 22:

            month_change = (
                (
                    closes.iloc[-1]
                    / closes.iloc[-22]
                ) - 1
            ) * 100

            result["month_change"] = month_change

        if len(closes) >= 200:

            year_change = (
                (
                    closes.iloc[-1]
                    / closes.iloc[0]
                ) - 1
            ) * 100

            result["year_change"] = year_change

        if not volumes.empty:

            current_volume = volumes.iloc[-1]

            result["volume"] = current_volume

            if len(volumes) >= 20:

                average_volume = (
                    volumes.iloc[-20:].mean()
                )

                result["average_volume"] = (
                    average_volume
                )

                if average_volume > 0:

                    result["relative_volume"] = (
                        current_volume
                        / average_volume
                    )

    except Exception as e:

        print(
            f"    [INFO] Could not get "
            f"extended market data for {ticker}: {e}"
        )

    return result


# ============================================================
# FUNDAMENTALS
# ============================================================

def get_fundamentals(
    ticker: str,
) -> dict[str, object]:

    fundamentals = {
        "market_cap": None,
        "pe": None,
        "forward_pe": None,
        "revenue_growth": None,
        "earnings_growth": None,
        "profit_margin": None,
        "operating_margin": None,
        "debt_to_equity": None,
        "free_cash_flow": None,
    }

    try:

        stock = yf.Ticker(ticker)
        info = stock.info

        fundamentals["market_cap"] = info.get(
            "marketCap"
        )

        fundamentals["pe"] = info.get(
            "trailingPE"
        )

        fundamentals["forward_pe"] = info.get(
            "forwardPE"
        )

        fundamentals["revenue_growth"] = info.get(
            "revenueGrowth"
        )

        fundamentals["earnings_growth"] = info.get(
            "earningsGrowth"
        )

        fundamentals["profit_margin"] = info.get(
            "profitMargins"
        )

        fundamentals["operating_margin"] = info.get(
            "operatingMargins"
        )

        fundamentals["debt_to_equity"] = info.get(
            "debtToEquity"
        )

        fundamentals["free_cash_flow"] = info.get(
            "freeCashflow"
        )

    except Exception as e:

        print(
            f"    [INFO] Could not get "
            f"fundamentals for {ticker}: {e}"
        )

    return fundamentals


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def clean_html(text: str) -> str:

    text = html.unescape(
        text or ""
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def get_google_news(
    ticker: str,
    company_name: str,
    max_articles: int = 8,
) -> list[dict[str, str]]:

    query = (
        f'"{company_name}" ({ticker}) stock'
    )

    encoded_query = urllib.parse.quote_plus(
        query
    )

    url = (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}"
        "&hl=en-US"
        "&gl=US"
        "&ceid=US:en"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/150 Safari/537.36"
            )
        },
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:

            xml_data = response.read()

    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
    ):

        return []

    try:

        root = ET.fromstring(
            xml_data
        )

    except ET.ParseError:

        return []

    articles = []

    for item in root.findall(".//item"):

        title = item.findtext(
            "title",
            "",
        )

        link = item.findtext(
            "link",
            "",
        )

        pub_date = item.findtext(
            "pubDate",
            "",
        )

        description = item.findtext(
            "description",
            "",
        )

        title = clean_html(title)

        description = clean_html(
            description
        )

        if not title:
            continue

        articles.append(
            {
                "title": title,
                "description": description,
                "link": link or "",
                "date": pub_date or "",
            }
        )

        if len(articles) >= max_articles:
            break

    return articles


# ============================================================
# FORMAT DATA FOR GEMMA
# ============================================================

def format_number(
    value,
    decimals: int = 2,
) -> str:

    if value is None:
        return "N/A"

    try:

        return f"{float(value):,.{decimals}f}"

    except (
        ValueError,
        TypeError,
    ):

        return "N/A"


def format_percent(
    value,
) -> str:

    if value is None:
        return "N/A"

    try:

        return f"{float(value) * 100:+.2f}%"

    except (
        ValueError,
        TypeError,
    ):

        return "N/A"


def build_market_context(
    price_context: dict[str, object],
) -> str:

    volume = price_context["volume"]

    average_volume = price_context[
        "average_volume"
    ]

    relative_volume = price_context[
        "relative_volume"
    ]

    return f"""
1-week change:
{format_percent(price_context["week_change"] / 100 if price_context["week_change"] is not None else None)}

1-month change:
{format_percent(price_context["month_change"] / 100 if price_context["month_change"] is not None else None)}

1-year change:
{format_percent(price_context["year_change"] / 100 if price_context["year_change"] is not None else None)}

Today's volume:
{format_number(volume, 0)}

20-day average volume:
{format_number(average_volume, 0)}

Relative volume:
{format_number(relative_volume, 2)}x
"""


def build_fundamental_context(
    fundamentals: dict[str, object],
) -> str:

    return f"""
Market cap:
{format_number(fundamentals["market_cap"], 0)}

P/E:
{format_number(fundamentals["pe"])}

Forward P/E:
{format_number(fundamentals["forward_pe"])}

Revenue growth:
{format_percent(fundamentals["revenue_growth"])}

Earnings growth:
{format_percent(fundamentals["earnings_growth"])}

Profit margin:
{format_percent(fundamentals["profit_margin"])}

Operating margin:
{format_percent(fundamentals["operating_margin"])}

Debt / equity:
{format_number(fundamentals["debt_to_equity"])}

Free cash flow:
{format_number(fundamentals["free_cash_flow"], 0)}
"""


# ============================================================
# GEMMA ANALYSIS
# ============================================================

def normalize_ai_report(
    text: str,
) -> str:

    text = re.sub(
        r"\s+",
        " ",
        (text or "").strip(),
    )

    text = text.replace(
        "**",
        "",
    )

    text = text.replace(
        "###",
        "",
    )

    verdict_match = re.search(
        r"Verdict:\s*(.*?)(?=\s+Reason:|$)",
        text,
        re.IGNORECASE,
    )

    reason_match = re.search(
        r"Reason:\s*(.*?)(?=\s+Risk:|$)",
        text,
        re.IGNORECASE,
    )

    risk_match = re.search(
        r"Risk:\s*(.*?)(?=$)",
        text,
        re.IGNORECASE,
    )

    if (
        verdict_match
        and reason_match
        and risk_match
    ):

        verdict = verdict_match.group(1).strip()
        reason = reason_match.group(1).strip()
        risk = risk_match.group(1).strip()

        return (
            f"Verdict: {verdict}\n"
            f"Reason: {reason}\n"
            f"Risk: {risk}"
        )

    return text.strip()


def get_ollama_analysis(
    ticker: str,
    company_name: str,
    percent_change: float,
    previous_close: float,
    current_price: float,
    price_context: dict[str, object],
    fundamentals: dict[str, object],
    news: list[dict[str, str]],
) -> str:

    model = OLLAMA_MODEL
    ollama_url = OLLAMA_URL

    if news:

        news_text = "\n".join(
            [
                (
                    f"Headline: {article['title']}\n"
                    f"Date: {article['date']}\n"
                    f"Snippet: {article['description']}"
                )
                for article in news
            ]
        )

    else:

        news_text = (
            "No recent Google News articles found."
        )

    market_context = build_market_context(
        price_context
    )

    fundamental_context = (
        build_fundamental_context(
            fundamentals
        )
    )

    prompt = f"""
You are analyzing whether a sharp stock decline may represent
an attractive quality dip-buying opportunity.

Company: {company_name}
Ticker: {ticker}

TODAY
Today's move: {percent_change:+.2f}%
Previous close: {previous_close:.2f}
Current price: {current_price:.2f}

MARKET CONTEXT
{market_context}

FUNDAMENTALS
{fundamental_context}

RECENT NEWS
{news_text}

DECISION RULES:

BUY THE DIP: Choose BUY THE DIP when:
- The business remains fundamentally strong.
- There is no clear structural threat to the business.
- Earnings/revenue/cash-flow outlook remains broadly intact.
- The selloff is mainly temporary news, market weakness, sector weakness, sentiment, or an apparent overreaction.
- The current price is more attractive because of the decline.

CONSIDER: Choose CONSIDER when:
- The company is potentially attractive, BUT there is meaningful uncertainty.
- There is a real risk that the problem is more serious than currently known.
- Fundamentals are mixed.
- Valuation is still high enough that further downside is plausible.
- The evidence is insufficient for a confident BUY THE DIP or AVOID.

CONSIDER IS NOT THE DEFAULT. Use CONSIDER only when there is a specific identifiable uncertainty. Do NOT choose CONSIDER simply because the stock fell sharply.

AVOID: Choose AVOID when:
- There is a clear structural problem with the business.
- Earnings/revenue/cash-flow outlook is materially deteriorating.
- Management significantly cuts guidance.
- A major competitive threat damages the company's future prospects.
- The stock remains clearly overvalued relative to its deteriorating outlook.
- The decline is justified by fundamentals rather than temporary sentiment.

IMPORTANT FOR THIS STRATEGY:
The investor is willing to accept meaningful short-term risk. Do NOT be overly conservative. Do NOT choose CONSIDER merely because there is uncertainty. Normal market uncertainty is acceptable. When the evidence supports a potentially attractive dip, prefer BUY THE DIP. When there is a serious fundamental problem, prefer AVOID. Use CONSIDER only for genuinely ambiguous cases.

FINAL DECISION:
Strong company + temporary/overreaction selloff = BUY THE DIP
Potentially good company + meaningful uncertainty = CONSIDER
Structural/fundamental deterioration or clearly unjustified valuation = AVOID

Choose exactly ONE:

BUY 
CONSIDER
AVOID

Return EXACTLY these three lines:

Verdict: [BUY / CONSIDER / AVOID]
Reason: [one concise sentence explaining the main reason]
Risk: [LOW / MEDIUM / HIGH / UNKNOWN]

Do NOT output Fundamentals.
Do NOT output any other fields.
Do not invent facts.
Do not give a price target.
Keep the entire response under 70 words.
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
        },
    }

    request = urllib.request.Request(
        ollama_url,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=180,
        ) as response:

            response_json = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except urllib.error.HTTPError as e:

        try:

            error_body = (
                e.read()
                .decode("utf-8")
            )

        except Exception:

            error_body = ""

        print(
            f"  [OLLAMA ERROR] "
            f"HTTP {e.code}: "
            f"{error_body}"
        )

        return "Ollama request failed."

    except urllib.error.URLError as e:

        print(
            f"  [OLLAMA ERROR] "
            f"Cannot connect: {e}"
        )

        return "Ollama is not running."

    except (
        json.JSONDecodeError,
        TimeoutError,
    ) as e:

        print(
            f"  [OLLAMA ERROR] {e}"
        )

        return "Ollama returned invalid data."

    try:

        content = response_json[
            "response"
        ]

    except (
        KeyError,
        TypeError,
    ):

        return (
            "Ollama returned "
            "an invalid response."
        )

    return normalize_ai_report(
        content
    )


# ============================================================
# COMPLETE ANALYSIS
# ============================================================

def get_news_reason(
    ticker: str,
    percent_change: float,
    previous_close: float,
    current_price: float,
) -> str:

    company_name = COMPANY_NAMES.get(
        ticker,
        ticker,
    )

    print(
        "    Loading market context..."
    )

    price_context = get_price_context(
        ticker
    )

    print(
        "    Loading fundamentals..."
    )

    fundamentals = get_fundamentals(
        ticker
    )

    print(
        "    Searching recent Google News..."
    )

    news = get_google_news(
        ticker=ticker,
        company_name=company_name,
        max_articles=8,
    )

    print(
        "    Sending data to Gemma 3:4b..."
    )

    return get_ollama_analysis(
        ticker=ticker,
        company_name=company_name,
        percent_change=percent_change,
        previous_close=previous_close,
        current_price=current_price,
        price_context=price_context,
        fundamentals=fundamentals,
        news=news,
    )


# ============================================================
# GOOGLE FINANCE CHART
# ============================================================

def get_google_finance_exchange(
    ticker: str,
) -> str:

    exchange_map = {
        "NMS": "NASDAQ",
        "NAS": "NASDAQ",
        "NGM": "NASDAQ",
        "NCM": "NASDAQ",
        "NYQ": "NYSE",
        "NYE": "NYSE",
        "ASE": "NYSEAMERICAN",
        "PCX": "NYSEARCA",
        "BTS": "NASDAQ",
        "BATS": "BATS",
    }

    try:

        stock = yf.Ticker(ticker)

        info = stock.info

        yahoo_exchange = info.get(
            "exchange"
        )

        if yahoo_exchange:

            return exchange_map.get(
                yahoo_exchange,
                yahoo_exchange,
            )

    except Exception as e:

        print(
            f"    [INFO] Could not determine "
            f"exchange for {ticker}: {e}"
        )

    return "NASDAQ"


def get_chart_link(
    ticker: str,
) -> str:

    exchange = get_google_finance_exchange(
        ticker
    )

    return (
        f"https://www.google.com/finance/quote/"
        f"{ticker}:{exchange}"
    )


# ============================================================
# EMAIL
# ============================================================

def send_email_alert(
    subject: str,
    body: str,
) -> None:

    msg = MIMEText(
        body,
        "plain",
        "utf-8",
    )

    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO

    with smtplib.SMTP(
        config.SMTP_SERVER,
        config.SMTP_PORT,
    ) as server:

        server.starttls()

        server.login(
            config.SMTP_USERNAME,
            config.SMTP_PASSWORD,
        )

        server.sendmail(
            config.EMAIL_FROM,
            [config.EMAIL_TO],
            msg.as_string(),
        )


# ============================================================
# CSV
# ============================================================

def log_alert_to_csv(
    ticker: str,
    percent_change: float,
    previous_close: float,
    current_price: float,
) -> None:

    file_exists = os.path.exists(
        config.LOG_CSV_PATH
    )

    with open(
        config.LOG_CSV_PATH,
        "a",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow(
                [
                    "timestamp",
                    "ticker",
                    "percent_change",
                    "previous_close",
                    "current_price",
                    "threshold_used",
                ]
            )

        writer.writerow(
            [
                datetime.now().isoformat(
                    timespec="seconds"
                ),
                ticker,
                round(
                    percent_change,
                    2,
                ),
                round(
                    previous_close,
                    2,
                ),
                round(
                    current_price,
                    2,
                ),
                config.VOLATILITY_THRESHOLD_PERCENT,
            ]
        )


# ============================================================
# REPORT
# ============================================================

def build_report_body(
    alerts: list[dict[str, object]],
    report_date: str,
) -> str:

    if not alerts:
        return "No volatility alerts today."

    lines = [
        "Trading Volatility Report",
        f"Date\t{report_date}",
        (
            f"Threshold\t"
            f"{config.VOLATILITY_THRESHOLD_PERCENT}%"
        ),
        "",
    ]

    for index, alert in enumerate(alerts):

        ticker = str(
            alert["ticker"]
        )

        company_name = COMPANY_NAMES.get(
            ticker,
            ticker,
        )

        analysis = str(
            alert["news_reason"]
        )

        verdict_match = re.search(
            r"Verdict:\s*(.*?)(?=\n|$)",
            analysis,
            re.IGNORECASE,
        )

        reason_match = re.search(
            r"Reason:\s*(.*?)(?=\n|$)",
            analysis,
            re.IGNORECASE,
        )

        risk_match = re.search(
            r"Risk:\s*(.*?)(?=\n|$)",
            analysis,
            re.IGNORECASE,
        )

        verdict = (
            verdict_match.group(1).strip()
            if verdict_match
            else "UNKNOWN"
        )

        reason = (
            reason_match.group(1).strip()
            if reason_match
            else "UNKNOWN"
        )

        risk = (
            risk_match.group(1).strip()
            if risk_match
            else "UNKNOWN"
        )

        company_name = re.sub(
            r"[\t\r\n]+",
            " ",
            company_name,
        )

        reason = re.sub(
            r"[\t\r\n]+",
            " ",
            reason,
        )

        lines.extend(
            [
                f"Company\t{company_name}",
                f"Ticker\t{ticker}",
                (
                    f"Change\t"
                    f"{alert['percent_change']:+.2f}%"
                ),
                (
                    f"Price\t"
                    f"{alert['current_price']:.2f}"
                ),
                f"Verdict\t{verdict}",
                f"Reason\t{reason}",
                f"Risk\t{risk}",
                f"Chart\t{alert['chart_link']}",
            ]
        )

        if index < len(alerts) - 1:

            lines.extend(
                [
                    "",
                    "--------------------------------",
                    "",
                ]
            )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def check_all_tickers() -> None:

    report_date = datetime.now().strftime(
        "%d.%m.%Y"
    )

    tickers = load_sp500_tickers()

    print(
        f"[{datetime.now()}] "
        f"Checking {len(tickers)} S&P 500 companies "
        f"(threshold: "
        f"{config.VOLATILITY_THRESHOLD_PERCENT}%)..."
    )

    bulk_data = download_sp500_data(
        tickers
    )

    alerts = []

    for ticker in tickers:

        try:

            (
                percent_change,
                previous_close,
                current_price,
            ) = get_intraday_drop_percent(
                bulk_data,
                ticker,
            )

        except Exception as e:

            print(
                f"  [INFO] {ticker}: "
                f"skipped - {e}"
            )

            continue

        if (
            percent_change
            > -config.VOLATILITY_THRESHOLD_PERCENT
        ):
            continue

        print(
            f"\n  {ticker}: "
            f"{percent_change:+.2f}%"
        )

        reason = get_news_reason(
            ticker=ticker,
            percent_change=percent_change,
            previous_close=previous_close,
            current_price=current_price,
        )

        print(
            "    Gemma 3:4b analysis:"
        )

        print(
            f"    {reason}"
        )

        chart_link = get_chart_link(
            ticker
        )

        alerts.append(
            {
                "ticker": ticker,
                "percent_change": percent_change,
                "previous_close": previous_close,
                "current_price": current_price,
                "news_reason": reason,
                "chart_link": chart_link,
            }
        )

        log_alert_to_csv(
            ticker,
            percent_change,
            previous_close,
            current_price,
        )

    alerts.sort(
        key=lambda alert: alert[
            "percent_change"
        ]
    )

    subject = (
        "Trading Volatility Report - "
        f"{report_date} - "
        f"{len(alerts)} alerts"
    )

    body = build_report_body(
        alerts,
        report_date,
    )

    try:

        send_email_alert(
            subject,
            body,
        )

        print(
            f"\nEmail report sent "
            f"({len(alerts)} alerts)."
        )

    except Exception as e:

        print(
            f"[EMAIL ERROR] {e}"
        )

    print("Done.")


def run_intraday_loop(
    interval_minutes: int = 30,
) -> None:

    while True:

        check_all_tickers()

        print(
            f"Waiting "
            f"{interval_minutes} minutes..."
        )

        time.sleep(
            interval_minutes * 60
        )


if __name__ == "__main__":

    check_all_tickers()

    # For continuous monitoring:
    # comment out check_all_tickers()
    # and uncomment:
    #
    # run_intraday_loop(interval_minutes=30)