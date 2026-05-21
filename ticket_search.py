import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# 1. INITIAL SETUP & PREFERENCES
st.set_page_config(page_title="MT Pockets Theatre", layout="wide")

CLIENT_ID = st.secrets['PAYPAL_CLIENT_ID']
CLIENT_SECRET = st.secrets['PAYPAL_CLIENT_SECRET']
ADMIN_PASSWORD = st.secrets.get('ADMIN_PASSWORD', 'mtpockets123')
PAYPAL_MODE = st.secrets.get('PAYPAL_MODE', 'live')

# Initialize the Google Sheets connection
conn = st.connection("gsheets", type=GSheetsConnection)

def get_access_token():
    base_url = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
    url = f"{base_url}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    try:
        response = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"}, headers=headers, timeout=10)
        return response.json().get('access_token')
    except:
        return None

# --- RECENT/LIVE TRANSACTION DATAFRAME (From Spreadsheet) ---
@st.cache_data(ttl=10)
def get_spreadsheet_transactions(_conn):
    try:
        df = _conn.read(worksheet="TransactionData")
        if df.empty:
            return pd.DataFrame()

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
    except:
        return pd.DataFrame()

# --- YOUR ORIGINAL HISTORICAL SEARCH ENGINE (Restored) ---
@st.cache_data(ttl=600)
def search_transactions_historical(prefix, start_date_str):
    token = get_access_token()
    if not token:
        return []

    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
    base_url = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"

    try:
        current_start = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        st.error(f"Error: Date format must be YYYY-MM-DD (Received: {start_date_str})")
        return []

    ultimate_end = datetime.now(timezone.utc)
    all_rows = []

    while current_start < ultimate_end:
        current_end = current_start + timedelta(days=30)
        if current_end > ultimate_end:
            current_end = ultimate_end

        params = {
            'start_date': current_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': current_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'fields': 'all',
            'page_size': 100
        }

        response = requests.get(f"{base_url}/v1/reporting/transactions", headers=headers, params=params)

        if response.status_code == 200:
            tx_data = response.json().get('transaction_details', [])
            for tx in tx_data:
                t_info = tx.get('transaction_info', {})
                p_info = tx.get('payer_info', {})
                items = tx.get('cart_info', {}).get('item_details', [])

                raw_time = t_info.get('transaction_initiation_date')
                if raw_time:
                    dt_obj = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                    f_date = dt_obj.strftime('%m-%d-%Y')
                    f_time = dt_obj.strftime('%H:%M:%S')
                else:
                    f_date, f_time = "N/A", "N/A"

                first_item_in_tx = True

                for item in items:
                    i_code = item.get('item_code', '')
                    i_name = item.get('item_name', '')

                    # Match by prefix from your original logic
                    if (i_code and i_code.startswith(prefix)) or (i_name and i_name.startswith(prefix)):
                        row_gross = float(item.get('item_amount', {}).get('value', 0))

                        # Handle the custom ticket show data option labels
                        checkout_opts = item.get('checkout_options', [{}])
                        show_date_val = checkout_opts[0].get('checkout_option_value', '-') if checkout_opts else "-"

                        # Normalized keys to perfectly match the matrix layout down below
                        all_rows.append({
                            'id': t_info.get('transaction_id', 'N/A'),
                            'date': f_date,
                            'time': f_time,
                            'name': p_info.get('payer_name', {}).get('alternate_full_name', 'N/A'),
                            'email': p_info.get('email_address', 'N/A'),
                            'amount': f"{row_gross:.2f}",
                            'item_name': i_name or 'Tickets',
                            'show_date': show_date_val,
                            'quantity': int(item.get('item_quantity', '1')),
                            'ada': 'No' # Default placeholder matching sheet
                        })
        current_start = current_end + timedelta(seconds=1)
    return all_rows

@st.cache_data(ttl=0)
def get_existing_checkins(_conn):
    try: return _conn.read(worksheet="CheckIns")
    except: return pd.DataFrame(columns=['Name', 'Status'])

# --- SIDEBAR CONTROLS ---
st.title("MT Pockets Ticket Search")

st.sidebar.header("Show Configuration")
ticket_prefix = st.sidebar.text_input("Ticket Prefix (e.g., LEAR)", "LEAR").strip()
start_date = st.sidebar.date_input("Start Date", datetime.today())

sort_col = st.sidebar.selectbox("Sort By", ["Name", "Date"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password")
is_admin = (password_input == ADMIN_PASSWORD)

if st.button("Refresh Manifest"):
    st.cache_data.clear()
    st.success("Manifest completely updated!")

# Pull data assets
df_spreadsheet = get_spreadsheet_transactions(conn)
df_checkins = get_existing_checkins(conn)

# --- SMART HYBRID SWITCH ---
today_date = datetime.today().date()
is_past_run = start_date < today_date

if is_past_run:
    # Trigger your exact original sequential loop
    historical_list = search_transactions_historical(ticket_prefix, start_date.strftime("%Y-%m-%d"))
    df_historical = pd.DataFrame(historical_list)
    df_combined = pd.concat([df_spreadsheet, df_historical], ignore_index=True).drop_duplicates(subset=['id'])
else:
    # Future/Today show run: protect speeds and pull only from the live spreadsheet
    df_combined = df_spreadsheet

# Global filter rule mapping
if not df_combined.empty and ticket_prefix:
    prefix_lower = ticket_prefix.lower()
    df_combined = df_combined[
        df_combined['item_name'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['show_date'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['id'].astype(str).str.lower().str.contains(prefix_lower)
    ]

# --- MATRIX DATA DISPLAY ---
if not is_admin:
    st.markdown('### WILL CALL MANIFEST')

    if not df_combined.empty:
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