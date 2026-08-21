# Bellhaven CRM Reconciliation

## What this project does

Bellhaven lists its current facilities on its website, but the CRM has some old names, wrong parent companies, duplicate accounts, and missing facilities. This project compares the website with the CRM and shows the differences in a small review app.

I used the website as the source for Bellhaven's current facility list. The system does not change the CRM on its own. It creates proposals and waits for a person to approve or reject each one.

## What I found

The website has 35 facilities. The main directory only shows 34 of them across three pages. Bellhaven Meadows of Findlay is linked from the homepage but is missing from the directory, so the scraper checks both places.

The CRM started with 121 accounts. The accounts API is paginated, so I fetch every page instead of assuming the first response is complete. I also find the Bellhaven parent account and retrieve all of its children directly.

The data included several different problems: old facility names, wrong parents, missing facilities, stale Bellhaven accounts, and multiple accounts for the same address. Marietta and Tiffin also needed the special CHOW process because they had both revenue history and outstanding AR.

## How I matched records

I compare address, ZIP, city, state, and facility name. Address and ZIP are the strongest evidence. Name similarity is useful for finding an old name, but I do not use it by itself to make a change.

For comparison, I normalize capitalization, punctuation, extra spaces, street abbreviations, and directional words. For example, `Road` and `Rd` are treated as the same. I still keep the original values so the reviewer can see exactly what came from the website and CRM.

The website and CRM use slightly different care terms. I map them as follows:

- Assisted Living to Assisted Living
- Memory Support to Memory Care
- Short-Term Rehabilitation & Nursing to Skilled Nursing

If a match is unclear, or several accounts match the same address, it goes to review instead of being changed automatically.

## Safety

Running `pipeline.py` never writes to the CRM. It only reads the website and CRM, saves a snapshot, and creates proposals. CRM writes only happen after the reviewer clicks APPROVE in the Streamlit app.

Before making an approved change, the app fetches the account again. If the CRM no longer matches the values that were shown to the reviewer, the app stops and marks the proposal as conflicted. This avoids overwriting a newer change.

Proposal IDs are deterministic, and decisions are saved in SQLite. If the pipeline runs again, the same approved or rejected proposal is not added to the pending queue again. Before creating an account, the app also checks whether an equivalent account already exists.

I never delete duplicate or stale records. A duplicate is marked Inactive and linked to the surviving account. A stale Bellhaven record is marked Needs Review and gets a note explaining why it was flagged.

## CHOW handling

Before changing a facility's parent, I check `lifetime_revenue` and `outstanding_ar`.

If the account has no revenue history, or its outstanding AR is zero, the existing account can be moved to the correct parent.

If both values are greater than zero, the old account must stay as it is for billing history. In that case, the app creates a new account under Bellhaven and sets `chow_current_account` on the old account. No other field on the old account is changed. I added a test specifically for this rule.

## Running it locally

Python 3.11 or newer is recommended.

Clone the repository and move into the project folder:

```powershell
git clone https://github.com/YOUR_USERNAME/bellhaven-reconciliation.git
cd bellhaven-reconciliation
```

Create a virtual environment, install the dependencies, and provide the CRM token:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:BELLHAVEN_API_TOKEN = "your-token"
```

Generate or refresh the reconciliation proposals:

```powershell
python pipeline.py --dry-run
```

Start the review application:

```powershell
streamlit run app.py
```

Then open `http://localhost:8501` in a browser. Press `Ctrl+C` in PowerShell when you want to stop the app.

The API token is read from `BELLHAVEN_API_TOKEN`. It is not stored in the code or committed to the repository.

Run the tests with:

```powershell
pytest -q
```

## Review process

The review app shows the website record, the current CRM record, why they were matched, and the proposed change. I reviewed one proposal at a time and checked that the address identified the same facility and that the proposed values matched the website.

For parent changes, I checked revenue and AR before approving. For duplicates, I checked every account at the address before choosing the survivor. After the reviews were complete, I fetched the CRM again and ran the pipeline one final time.

## Final result

The CRM finished with 126 accounts. Five accounts were created: Amberly Manor, Bellhaven of Batavia, Bellhaven of Carlisle, and the new Bellhaven accounts needed for the Marietta and Tiffin CHOW cases.

The final run found all 35 website facilities correctly matched. It produced no new proposals, no missing facilities, no unresolved CHOW cases, no pending decisions, and no conflicts. The review history contains 34 approved decisions and 5 rejected decisions.

The old Marietta and Tiffin accounts stayed under Cedar Trail with their financial history unchanged, and both point to the correct new Bellhaven accounts. Duplicate accounts were marked Inactive and linked to their survivors. Alliance, Coldwater, and Sandusky were kept in the CRM with Needs Review status and a note.

## Daily run

The example GitHub Actions workflow in `.github/workflows/daily.yml` runs the pipeline once a day in dry-run mode. It only creates proposals and snapshots. It cannot approve changes.

SQLite is enough for this local project. If this were deployed for regular use, I would move the proposal history to shared storage and add authentication, alerts, better monitoring, and managed secrets.

## Assumptions and tradeoffs

I verified the API pagination and field values from the live sandbox before building the matcher. Missing optional values are returned as empty strings. The valid statuses are Active, Inactive, and Needs Review.

I kept the solution deliberately small. I did not use an LLM, Docker, microservices, or a complicated entity-resolution framework. For this dataset, simple matching rules, clear evidence, and human approval were easier to explain and safer to use.

## Time spent

I spent about 1 hour and 20 minutes of focused time on discovery, implementation, testing, review support, and final verification. This does not include time spent waiting for manual review decisions.
