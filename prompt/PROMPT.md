# 50fin MFC API — build brief

Hi. I'd like you to build a small internal web tool for me, end to end, working through the stages below in order.

Everything you need is already in this project. This file is at `prompt/PROMPT.md`, and alongside it is `prompt/MFC Unlien.postman_collection.json`. Read the collection before you write anything — the endpoint URL, the HTTP method, the three headers and the request body shape all come from that file rather than from anything you assume.

Read this whole brief first so you understand where it's going, then work through the stages one at a time. Commit at the end of each stage. There's exactly one place I want you to stop and wait for me, called out in Stage 1; everywhere else, keep going.

---

## What I'm building

Someone on my team opens a page, types in a PAN and a mobile number, and sees that investor's mutual fund lien holdings in two tables — the raw holdings, and an aggregated view of them.

It's an internal tool. No login, no user accounts, no database. One form, one API call, one result screen.

FastAPI for the backend, Streamlit for the frontend, run as two separate processes side by side.

---

## Project structure

The root folder `50fin MFC API` already exists, with the `prompt/` folder inside it holding this brief and the collection. Build everything else at the top level of the root — keep the layout flat. No `src/`, no packages, no `__init__.py`.

```
50fin MFC API/
├── prompt/
│   ├── PROMPT.md                              (this file)
│   └── MFC Unlien.postman_collection.json     (contains live credentials — see below)
├── .env                  real values, never committed
├── .env.example          dummy values, committed
├── .gitignore
├── README.md
├── requirements.txt
├── config.py             settings loaded from environment
├── api_client.py         the shared HTTP client and upstream calls
├── backend.py            FastAPI app
├── transforms.py         dataframe building and aggregation
└── app.py                Streamlit UI
```

`prompt/` is reference material and the only subfolder. If you find yourself wanting to add another directory, don't.

**Important:** the Postman collection has real `clientId` and `clientSecret` values inside it. It must never reach GitHub. Add `prompt/MFC Unlien.postman_collection.json` to `.gitignore` in Stage 1, alongside `.env`. Keep `prompt/PROMPT.md` committed — it's useful documentation and contains nothing secret.

---

## How credentials are handled

This matters more than it looks, so I want it done deliberately.

`clientId` and `clientSecret` are the same for every call to this API, and they'll be rotated at some point. `Content-Type: application/json` is also the same every time, but it isn't a secret.

All three belong in one place. In `config.py`, read the credentials and the base URL from environment variables. In `api_client.py`, create **one** `httpx.AsyncClient` at application startup with those three headers set as its default headers, and reuse it for the lifetime of the process. Every request made through that client then carries the auth automatically.

The point is that when I add a second endpoint later, I should be writing only a path and a body — never another copy of the header dictionary. Don't build headers inline inside a request function, and don't create a new client per request.

Copy the credential values out of the collection into `.env`. Rotating them should then mean editing `.env` and restarting, with no code change anywhere. Nothing secret gets hardcoded, and nothing secret appears in `.env.example`.

If the credentials are missing from the environment, the backend should say so clearly at startup rather than failing later with a confusing 401.

---

## The response shape

This is what the API returns. Values left blank on purpose — I'm giving you the structure, not real data.

```
{
  "code": ,
  "detail": ,
  "data": {
    "reqId": ,
    "pan": ,
    "pekrn": ,
    "email": ,
    "mobile": ,
    "clientId": ,
    "clientName": ,
    "lenderCode": ,
    "data": [
      {
        "rtaName": , "lienRefNo": , "amc": , "folio": , "schemeName": ,
        "isin": , "lienHoldUnits": , "lienSubRefNo": , "TotalLienUnits": , "schemeCode":
      }
    ]
  }
}
```

Four things to be careful about:

The holdings array is nested two levels deep at `data.data`. The outer `data` is an object of investor-level fields; the inner `data` is the list of rows. Don't flatten them together.

`TotalLienUnits` is spelled with a capital T while the other nine fields are camelCase. That's the API's spelling, not my typo. Read it exactly as written, don't normalise it.

`lienHoldUnits` and `TotalLienUnits` are numeric but may arrive as strings. Coerce them safely and don't crash on nulls or unparseable values.

Check `code` to decide whether the call actually succeeded, rather than relying on the HTTP status alone.

Expect 100–250 rows. Small enough that plain dataframes are fine — no pagination, no virtualisation.

---

# Stage 1 — Repository and scaffolding

The root folder exists already, so work inside it. Initialise a git repository there.

**Commit the `.gitignore` first, before anything else.** It must exclude `.env`, `prompt/MFC Unlien.postman_collection.json`, `__pycache__/`, `*.pyc` and the virtualenv directory. I want zero chance of either the credentials file or `.env` entering history.

Then create the skeleton: the remaining files from the structure above, empty or stubbed, plus `requirements.txt` with pinned versions and `.env.example` listing every variable with dummy values. Create `.env` too with the real values pulled from the collection.

Before committing anything else, run `git status` and confirm out loud that neither `.env` nor the collection appears in it.

**Stop here.** Show me the `git remote add` and `git push` commands I'll need. I'll create the GitHub repository and push, then tell you to continue. This is the only point where you should wait for me.

# Stage 2 — Config and API client

`config.py`: read the credentials, the upstream URL and a request timeout from the environment. One settings object, imported wherever needed. Fail loudly and clearly if a required credential is absent.

`api_client.py`: the shared client described above, created at startup and closed at shutdown, with one async function that performs the lookup. Handle timeouts, connection failures, and a non-JSON error page arriving where JSON was expected.

# Stage 3 — Backend

`backend.py`: a FastAPI app with a POST endpoint that takes a PAN and mobile number, calls the client, and returns the response.

Validate on the way in with a Pydantic model, so the API can't be called with junk even if someone bypasses the UI:

- PAN — five letters, four digits, one letter. Uppercase it.
- Mobile — ten digits starting with 6, 7, 8 or 9. Strip spaces, dashes, a `+91` prefix or a leading zero first.

Add a health endpoint that reports whether credentials loaded, so I can tell a missing `.env` apart from a rejected key.

Translate upstream failures into readable messages: 401 as a credentials problem, 400 as rejected input, a timeout as a timeout. Never leak a raw stack trace to the frontend.

# Stage 4 — Form and result header

`app.py`: the two input fields and a submit button, with the same validation as the backend so the person gets an instant, specific message. Errors say what to fix.

The call must fire only on an explicit click. Streamlit reruns the whole script on every widget interaction, so be careful not to end up calling the API on every keystroke. Store the result in `st.session_state` so later filtering re-renders from memory and never re-fires the request.

Above the tables, show the investor-level fields — client name, PAN, mobile, email, client ID, lender code, request ID — as a compact header block. Not a table; a clean summary so it's obvious whose portfolio is on screen. Keep `code` and `detail` unobtrusive.

# Stage 5 — Table 1, holdings

In `transforms.py`, build the dataframe from `data.data`. In `app.py`, render it: one row per record, all ten columns, readable headers (RTA, Lien ref no., AMC, Folio, Scheme name, ISIN, Lien hold units, Lien sub ref no., Total lien units, Scheme code) while the underlying field names stay intact in code. Numbers right-aligned and formatted sensibly for fund units.

Filters directly above the table:

- Sort by — dropdown of all ten columns, plus ascending/descending
- Text search across rows, so someone can paste an ISIN or part of a scheme name
- Multi-selects for the repeated-value columns: RTA, AMC, scheme name, folio
- A clear-filters reset

Filter a copy, never the original, so clearing restores everything without another API call. Show a row count like "38 of 212 holdings". CSV download beneath the table.

# Stage 6 — Table 2, aggregation

A second table below the first, aggregating the same data. The rule:

**Group on four fields together — `isin`, `folio`, `lienSubRefNo` and `lienRefNo` — and sum `lienHoldUnits`.** Where rows match on all four, their units add into one row. If any one of the four differs, the rows stay separate.

That's a plain group-by; rows already unique on that combination pass through unchanged, because a group of one sums to itself. Don't write special-case logic to detect singletons.

`rtaName`, `amc`, `schemeName` and `schemeCode` should be constant within a group, so carry them through by taking the first value.

**Do not sum `TotalLienUnits`.** The name says it's already a total for the lien, so adding it across a group would double count. Take the first value, like the descriptive columns. If it turns out to vary within a group, that's a real data inconsistency — surface it as a warning rather than quietly averaging it away.

Add a count column showing how many original rows went into each aggregated row.

Two checks I want built in, not assumed:

The sum of `lienHoldUnits` across the aggregated table must exactly equal the sum across the raw table. Aggregation moves units between rows; it never creates or destroys them. Show both totals, or assert it.

Nulls in any grouping key must not silently drop rows. Pandas `groupby` excludes NaN keys by default, which would make rows vanish from table 2 with no indication. Use `dropna=False` or handle nulls explicitly, and tell me how many rows were affected.

Table 2 gets its own independent filters, same pattern as table 1. Changing a filter on one table must not affect the other, and neither may re-fire the API call.

# Stage 7 — Polish and README

Clean and readable is the priority throughout: generous spacing, scannable tables, no clutter, sentence case labels, plain language.

Make sure every failure produces a message that says what happened and what to do — "the backend isn't running, start it with this command", not a stack trace. Cover: backend down, timeout, bad credentials, rejected PAN, non-JSON error page, and a successful call returning an empty holdings array. That last one is a normal outcome, not an error; say plainly that this PAN has no lien holdings rather than showing an empty grid.

Finish `README.md`: what the tool does, install steps, how to fill in `.env`, how to rotate credentials, and the exact commands to start both processes.

---

## Out of scope for now

Deployment. I'll handle hosting as a separate task once this works. Just write it so that's easy later — every URL and port driven by environment variables, nothing hardcoded to localhost.

## One caution

The endpoint path suggests this POST creates something server-side rather than being a read-only lookup. Assume it isn't safe to call repeatedly with the same input: no automatic retries, no background refresh, nothing fires without a click.
