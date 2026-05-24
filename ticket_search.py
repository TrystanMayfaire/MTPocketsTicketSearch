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
            checkout_val = row.get('raw_checkout_val', row.get('show_date', ''))

            # Smart Date Sanitizer: Converts manual sheet inputs gracefully to %m-%d-%Y
            raw_sheet_date = str(row.get('date', ''))
            try:
                parsed_dt = pd.to_datetime(raw_sheet_date)
                formatted_date = parsed_dt.strftime('%m-%d-%Y')
            except:
                formatted_date = datetime.today().strftime('%m-%d-%Y')

            # Aligned column extractions for the 12-column ledger
            item_id_val = row.get('item_id', row.get('Item ID', row.get('Show Code', 'N/A')))
            if len(row) >= 9 and (str(item_id_val).startswith('O-') or str(item_id_val).startswith('TXN')):
                item_id_val = row.iloc[8]

            standardized_rows.append({
                'item id': str(item_id_val).strip(),
                'date': formatted_date,
                'time': str(row.get('time', '00:00:00')),
                'name': str(row.get('name', '')).strip(),
                'email address': str(row.get('email', '')),
                'gross': str(row.get('gross', row.get('amount', '0.00'))),
                'fee': str(row.get('fee', '0.00')),
                'net': str(row.get('net', row.get('amount', '0.00'))),
                'item_name': str(row.get('item_name', 'Tickets')),
                'raw_checkout_val': str(checkout_val),
                'quantity': int(row.get('quantity', 1)) if pd.notna(row.get('quantity')) else 1,
            })
        return pd.DataFrame(standardized_rows)
    except Exception as e:
        st.sidebar.error(f"Spreadsheet parsing debug notice: {e}")
        return pd.DataFrame()

# --- HISTORICAL SEARCH ENGINE ---
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

                overall_fee = float(t_info.get('fee_amount', {}).get('value', 0))
                if overall_fee > 0: overall_fee = -overall_fee
                first_item_in_tx = True

                for item in items:
                    i_code = item.get('item_code', '')
                    i_name = item.get('item_name', '')

                    if (i_code and i_code.startswith(prefix)) or (i_name and i_name.startswith(prefix)):
                        row_gross = float(item.get('item_amount', {}).get('value', 0))
                        row_fee = overall_fee if first_item_in_tx else 0.0
                        row_net = row_gross + row_fee
                        first_item_in_tx = False

                        checkout_opts = item.get('checkout_options', [{}])
                        show_date_val = checkout_opts[0].get('checkout_option_value', 'N/A') if checkout_opts else "N/A"

                        all_rows.append({
                            'date': f_date,
                            'time': f_time,
                            'name': p_info.get('payer_name', {}).get('alternate_full_name', 'N/A'),
                            'email address': p_info.get('email_address', 'N/A'),
                            'item id': i_code or i_name,
                            'gross': f"{row_gross:.2f}",
                            'fee': f"{row_fee:.2f}",
                            'net': f"{row_net:.2f}",
                            'quantity': int(item.get('item_quantity', '1')),
                            'item_name': i_name or 'Tickets',
                            'raw_checkout_val': show_date_val,
                        })
        current_start = current_end + timedelta(seconds=1)
    return all_rows

@st.cache_data(ttl=0)
def get_existing_checkins(_conn):
    try: return _conn.read(worksheet="CheckIns")
    except: return pd.DataFrame(columns=['Name', 'Status'])

# --- SIDEBAR CONTROLS ---
st.sidebar.header("Show Configuration")
ticket_prefix = st.sidebar.text_input("Ticket Prefix (e.g., LEAR)", "LEAR").strip()
start_date = st.sidebar.date_input("Start Date", datetime.today())

sort_col = st.sidebar.selectbox("Sort By", ["Name", "Date", "Ticket ID"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password")
is_admin = (password_input == ADMIN_PASSWORD)

if st.button("Refresh Manifest"):
    st.cache_data.clear()
    st.success("Manifest completely updated!")

# Pull data sources
df_spreadsheet = get_spreadsheet_transactions(conn)

# --- SMART HYBRID SWITCH ---
today_date = datetime.today().date()
is_past_run = start_date < today_date

if is_past_run:
    historical_list = search_transactions_historical(ticket_prefix, start_date.strftime("%Y-%m-%d"))
    df_historical = pd.DataFrame(historical_list)
    if not df_spreadsheet.empty and not df_historical.empty:
        df_combined = pd.concat([df_spreadsheet, df_historical], ignore_index=True)
    elif not df_historical.empty:
        df_combined = df_historical
    else:
        df_combined = df_spreadsheet
else:
    df_combined = df_spreadsheet

# Global filter rule shield
if not df_combined.empty and ticket_prefix:
    prefix_lower = ticket_prefix.lower()
    df_combined = df_combined[
        df_combined['item_name'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['raw_checkout_val'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['item id'].astype(str).str.lower().str.contains(prefix_lower) |
        (df_combined['item_name'] == '') | (df_combined['item_name'] == 'nan')
    ]

# --- TRANSFORMATION & SEPARATION GRID ENGINE ---
if not df_combined.empty:
    df = df_combined.copy()

    first_item_name = df['item_name'].iloc[0]
    show_title = str(first_item_name).replace(" Tickets", "").upper()

    def extract_manifest_details(row):
        raw_show_date = str(row['raw_checkout_val'])
        show_date = raw_show_date[:raw_show_date.rfind(",")] if "," in raw_show_date else raw_show_date
        full_name = str(row['name']).strip()
        last_name = full_name.split()[-1] if " " in full_name else full_name
        return pd.Series([show_date, last_name])

    df[['Show Date', 'Last Name']] = df.apply(extract_manifest_details, axis=1)
    df['gross'] = pd.to_numeric(df['gross'], errors='coerce')
    df['fee'] = pd.to_numeric(df['fee'], errors='coerce')
    df['net'] = pd.to_numeric(df['net'], errors='coerce')
    df['temp_date'] = pd.to_datetime(df['date'], format='%m-%d-%Y', errors='coerce')

    ascending = (sort_order == "Ascending")
    if sort_col == "Date":
        df = df.sort_values(by=['temp_date', 'time'], ascending=ascending)
    elif sort_col == "Name":
        df = df.sort_values(by='name', ascending=ascending)
    elif sort_col == "Ticket ID":
        df = df.sort_values(by='item id', ascending=ascending)

    df = df.drop(columns=['temp_date'])

    if not is_admin:
        st.markdown(f'### {show_title}')

        raw_dates = df['Show Date'].unique()
        sorted_dates = sorted(raw_dates, key=lambda x: pd.to_datetime(x, errors='coerce'))
        filter_date = st.selectbox(
            "Filter by Show Date",
            ["All"] + [str(d) for d in sorted_dates]
        )

        display_df = df.copy()
        if filter_date != "All":
            display_df = display_df[display_df['Show Date'] == filter_date]

        # First group the rows to aggregate clean ticket sums per person per date
        grouped_df = display_df.groupby(['Last Name', 'name', 'Show Date'], as_index=False)['quantity'].sum()

        # --- RESTORED MULTI-NIGHT BRACKET TRACKING ---
        # 1. Map out which dates each individual person is attending across the whole dashboard scope
        person_dates_map = display_df.groupby('name')['Show Date'].unique().to_dict()

        def format_purchaser_label(row):
            p_name = row['name']
            all_my_dates = sorted(list(person_dates_map.get(p_name, [])))

            # If the patron has tickets for multiple nights, calculate their timeline string
            if len(all_my_dates) > 1:
                try:
                    current_index = all_my_dates.index(row['Show Date']) + 1
                    return f"{p_name} [Night {current_index}/{len(all_my_dates)}]"
                except ValueError:
                    return p_name
            # If they are just attending a single evening, display their plain unbracketed name
            return p_name

        grouped_df['Custom Label'] = grouped_df.apply(format_purchaser_label, axis=1)

        # Build pivot table breakout tracking matrix
        manifest = grouped_df.pivot_table(
            index=['Last Name', 'Custom Label', 'Show Date'],
            columns='Show Date',
            values='quantity',
            aggfunc='sum'
        ).reset_index()

        manifest = manifest.sort_values(by=['Last Name', 'Show Date']).rename(columns={'Custom Label': 'Purchaser Name'}).drop(columns=['Last Name', 'Show Date'])
        date_cols = [c for c in manifest.columns if c != 'Purchaser Name']

        date_cols.sort(key=lambda x: pd.to_datetime(x, errors='coerce'))
        manifest = manifest[['Purchaser Name'] + date_cols]

        try:
            existing_checkins = get_existing_checkins(conn)
            manifest['Checked In'] = manifest['Purchaser Name'].isin(existing_checkins['Name'].tolist())
        except:
            manifest['Checked In'] = False

        manifest = manifest[['Checked In'] + [c for c in manifest.columns if c != 'Checked In']]

        # --- TOTALS ---
        manifest_numeric = manifest.fillna(0)
        totals_row = {"Purchaser Name": "TOTAL TICKETS SOLD", "Checked In": False}
        for col in date_cols:
            totals_row[col] = manifest_numeric[col].sum()
        manifest = pd.concat([manifest, pd.DataFrame([totals_row])], ignore_index=True)

        manifest['Purchaser Name'] = manifest.apply(
            lambda r: f"✅ {r['Purchaser Name']}" if r['Checked In'] == True and r['Purchaser Name'] != "TOTAL TICKETS SOLD" else r['Purchaser Name'],
            axis=1
        )

        for col in date_cols:
            manifest[col] = (
                manifest[col]
                .fillna(0)
                .astype(float