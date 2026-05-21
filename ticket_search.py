import requests
import csv
import argparse
import sys
import requests
from datetime import datetime, timedelta, timezone
import streamlit as st
import pandas as pd
import requests
from streamlit_gsheets import GSheetsConnection

# 1. MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(page_title="MT Pockets Theatre", layout="wide")

# 2. LOAD SECRETS
CLIENT_ID = st.secrets['PAYPAL_CLIENT_ID']
CLIENT_SECRET = st.secrets['PAYPAL_CLIENT_SECRET']
ADMIN_PASSWORD = st.secrets.get('ADMIN_PASSWORD', 'mtpockets123')
PAYPAL_MODE = st.secrets.get('PAYPAL_MODE', 'live')

# 3. INITIALIZE CONNECTION (Global)
conn = st.connection("gsheets", type=GSheetsConnection)

# --- RECENT/LIVE TRANSACTION DATAFRAME (From Pipedream Spreadsheet backend) ---
@st.cache_data(ttl=10)
def get_spreadsheet_transactions(_conn):
    try:
        df = _conn.read(worksheet="TransactionData")
        if df.empty:
            return pd.DataFrame()

        # Standardize spreadsheet column mappings to match your original PayPal dict keys
        standardized_rows = []
        for _, row in df.iterrows():
            standardized_rows.append({
                'id': str(row.get('transaction_id', '')),
                'date': str(row.get('date', '')),
                'time': str(row.get('time', '')),
                'name': str(row.get('name', '')).strip(),
                'email': str(row.get('email', '')),
                'amount': str(row.get('amount', '0.00')),
                'item_name': str(row.get('item_name', '')),
                'show_date': str(row.get('show_date', '-')),
                'quantity': int(row.get('quantity', 1)),
                'ada': str(row.get('ada_seating', 'No'))
            })
        return pd.DataFrame(standardized_rows)
    except Exception as e:
        return pd.DataFrame()

# --- HISTORICAL PAYPAL API ENGINE (Your original reporting search loop) ---
def get_access_token():
    base_url = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
    url = f"{base_url}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    try:
        response = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"}, headers=headers, timeout=10)
        return response.json().get('access_token')
    except:
        return None

@st.cache_data(ttl=300) # Keep historical requests cached for speed
def search_paypal_historical_records(prefix, start_date_val):
    token = get_access_token()
    if not token:
        return pd.DataFrame()

    base_url = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
    url = f"{base_url}/v1/reporting/transactions"

    # Format the start date from your sidebar widget into PayPal's required timestamp string
    start_timestamp = f"{start_date_val.strftime('%Y-%m-%d')}T00:00:00Z"

    params = {
        "start_date": start_timestamp,
        "end_date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fields": "all",
        "page_size": 100
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            return pd.DataFrame()

        tx_details = response.json().get('transaction_details', [])
        historical_rows = []

        for tx in tx_details:
            info = tx.get('transaction_info', {})
            payer = tx.get('payer_info', {})
            item = info.get('item_details', [{}])[0]

            # Reconstruct original option and item prefix parsing logic
            item_name = item.get('item_name', 'Tickets')
            options = item.get('item_options', [])
            show_date = "-"
            ada = "No"
            for opt in options:
                opt_name = opt.get('name', '').lower()
                if 'date' in opt_name or 'performance' in opt_name:
                    show_date = opt.get('value', '-')
                if 'ada' in opt_name or 'seating' in opt_name:
                    ada = opt.get('value', 'No')

            # Filter records using your original target criteria (Prefix text match)
            if prefix.lower() in item_name.lower() or prefix.lower() in show_date.lower():
                historical_rows.append({
                    'id': info.get('transaction_id', ''),
                    'date': info.get('transaction_initiation_date', '')[:10],
                    'time': info.get('transaction_initiation_date', '')[11:19],
                    'name': f"{payer.get('given_name', '')} {payer.get('surname', '')}".strip(),
                    'email': payer.get('email_address', ''),
                    'amount': info.get('transaction_amount', {}).get('value', '0.00'),
                    'item_name': item_name,
                    'show_date': show_date,
                    'quantity': int(item.get('item_quantity', 1)),
                    'ada': ada
                })
        return pd.DataFrame(historical_rows)
    except:
        return pd.DataFrame()

@st.cache_data(ttl=0)
def get_existing_checkins(_conn):
    try: return _conn.read(worksheet="CheckIns")
    except: return pd.DataFrame(columns=['Name', 'Status'])

# --- MAIN UI ---
st.title("MT Pockets Ticket Search")

st.sidebar.header("Show Configuration")
ticket_prefix = st.sidebar.text_input("Ticket Prefix", "LEAR").strip()
start_date = st.sidebar.date_input("Start Date", datetime.today())
sort_col = st.sidebar.selectbox("Sort By", ["Name", "Date"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password")
is_admin = (password_input == ADMIN_PASSWORD)

if st.button("Refresh Manifest"):
    st.cache_data.clear()
    st.success("Manifest completely updated from data sources!")

# Fetch the modern local spreadsheet transactions
df_spreadsheet = get_spreadsheet_transactions(conn)
df_checkins = get_existing_checkins(conn)

# --- AUTOMATIC HYBRID SELECTION LOGIC ---
today_date = datetime.today().date()
is_past_run = start_date < today_date

if is_past_run:
    # Rule 1: Selected start date is in the past -> Pull PayPal Cloud Backup and append to Spreadsheet
    df_historical_raw = search_paypal_historical_records(ticket_prefix, start_date)
    df_combined = pd.concat([df_spreadsheet, df_historical_raw], ignore_index=True).drop_duplicates(subset=['id'])
else:
    # Rule 2: Show date is today or future -> Skip the laggy API entirely and trust the spreadsheet
    df_combined = df_spreadsheet

# Filter data to only show rows matching the target prefix chosen in the sidebar
if not df_combined.empty and ticket_prefix:
    pass
    # prefix_lower = ticket_prefix.lower()
    #
    # # Force all columns to strings safely to prevent tracking errors on missing data
    # df_combined = df_combined[
    #     df_combined['item_name'].astype(str).str.lower().str.contains(prefix_lower) |
    #     df_combined['show_date'].astype(str).str.lower().str.contains(prefix_lower) |
    #     df_combined['id'].astype(str).str.lower().str.contains(prefix_lower) |
    #     df_combined['name'].astype(str).str.lower().str.contains(prefix_lower)
    # ]

# Rest of your original quantity explosion, data editor, and check-in save mechanics remain identical
if not is_admin:
    st.markdown('### WILL CALL MANIFEST')

    if not df_combined.empty:
        # Collect dynamic dropdown options from the filtered subset
        sorted_date_options = sorted(list(df_combined['show_date'].dropna().unique()))
        filter_date = st.selectbox("Filter by Show Date", ["All"] + [str(d) for d in sorted_date_options])

        if filter_date != "All":
            df_combined = df_combined[df_combined['show_date'] == filter_date]

        expanded_rows = []
        for _, row in df_combined.iterrows():
            qty = row['quantity']
            for i in range(qty):
                row_copy = row.copy()
                row_copy['Clean Name'] = f"{str(row['name'])} (Ticket {i+1}/{qty})" if qty > 1 else str(row['name'])
                expanded_rows.append(row_copy)

        display_df = pd.DataFrame(expanded_rows)

        def get_last_name(full_name):
            base_name = full_name.split(" (Ticket")[0]
            return base_name.split()[-1] if " " in base_name else base_name

        display_df['Last Name'] = display_df['Clean Name'].apply(get_last_name)

        ascending = (sort_order == "Ascending")
        if sort_col == "Name":
            display_df = display_df.sort_values(by='Last Name', ascending=ascending)
        elif sort_col == "Date":
            display_df = display_df.sort_values(by=['date', 'time'], ascending=ascending)

        manifest = display_df.pivot_table(index=['Last Name', 'Clean Name'], columns='show_date', values='id', aggfunc='count').reset_index()
        manifest = manifest.rename(columns={'Clean Name': 'Purchaser Name'}).drop(columns=['Last Name'])
        date_cols = [c for c in manifest.columns if c != 'Purchaser Name']

        manifest['Checked In'] = manifest['Purchaser Name'].isin(df_checkins['Name'].tolist())
        manifest = manifest[['Checked In'] + [c for c in manifest.columns if c != 'Checked In']]

        manifest_numeric = manifest.fillna(0)
        totals_row = {"Purchaser Name": "TOTAL TICKETS SOLD", "Checked In": False}
        for col in date_cols:
            totals_row[col] = manifest_numeric[col].sum()
        manifest = pd.concat([manifest, pd.DataFrame([totals_row])], ignore_index=True)

        manifest['Purchaser Name'] = manifest.apply(
            lambda r: f"✅ {r['Purchaser Name']}" if r['Checked In'] == True and r['Purchaser Name'] != "TOTAL TICKETS SOLD" else r['Purchaser Name'], axis=1
        )

        for col in date_cols:
            manifest[col] = manifest[col].fillna(0).astype(float).astype(int).astype(str).replace('0', '-')

        st.info("Check boxes to mark arrivals at the door and click Save Changes.")

        config = {
            "Checked In": st.column_config.CheckboxColumn("Arrived", width="small"),
            "Purchaser Name": st.column_config.Column("Purchaser Name", width=280, disabled=True)
        }
        for col in date_cols:
            config[col] = st.column_config.Column(col, width=150, alignment="center", disabled=True)

        edited_df = st.data_editor(manifest, column_config=config, hide_index=True, key="manifest_editor")

        if st.button("Save Changes to Google Sheet"):
            with st.spinner("Writing check-ins..."):
                checkin_list = edited_df[(edited_df['Checked In'] == True) & (edited_df['Purchaser Name'] != "TOTAL TICKETS SOLD")][['Purchaser Name']]
                checkin_list.columns = ['Name']
                checkin_list['Status'] = 'Checked In'
                conn.update(worksheet="CheckIns", data=checkin_list)
                st.cache_data.clear()
                st.success("Changes deployed successfully!")
                st.rerun()
    else:
        st.warning(f"No transactions found matching the prefix '{ticket_prefix}' in this timeline window.")
else:
    st.success("Admin Overview Dashboard")
    if not df_spreadsheet.empty:
        st.dataframe(df_spreadsheet, hide_index=True)