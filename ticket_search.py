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
        df = _conn.query('SELECT * FROM TransactionData')
        if df is None or df.empty:
            return pd.DataFrame()
        
        standardized_rows = []
        for _, row in df.iterrows():
            row_dict = {str(k).strip().lower(): v for k, v in row.items()}
            
            show_date_raw = str(row_dict.get('show_date', row_dict.get('show date', ''))).strip()
            item_name_val = str(row_dict.get('item_name', row_dict.get('item name', 'Tickets'))).strip()
            tx_id = str(row_dict.get('transaction_id', row_dict.get('transaction id', 'N/A'))).strip()
            gross_val = str(row_dict.get('amount', '0.00')).strip()
            raw_sheet_date = str(row_dict.get('date', ''))
            
            try:
                parsed_dt = pd.to_datetime(raw_sheet_date)
                formatted_date = parsed_dt.strftime('%m-%d-%Y')
            except:
                formatted_date = datetime.today().strftime('%m-%d-%Y')

            standardized_rows.append({
                'item id': tx_id,
                'date': formatted_date,
                'time': str(row_dict.get('time', '00:00:00')),
                'name': str(row_dict.get('name', '')).strip(),
                'email address': str(row_dict.get('email', 'N/A')),
                'gross': gross_val,
                'fee': '0.00',
                'net': gross_val,
                'item_name': item_name_val,
                'raw_checkout_val': show_date_raw, 
                'quantity': int(row_dict.get('quantity', 1)) if pd.notna(row_dict.get('quantity')) else 1,
            })
        return pd.DataFrame(standardized_rows)
    except:
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
    try: 
        df = _conn.query('SELECT * FROM CheckIns')
        return df if df is not None else pd.DataFrame(columns=['Name', 'Status'])
    except: 
        return pd.DataFrame(columns=['Name', 'Status'])

# --- SIDEBAR CONTROLS ---
st.sidebar.header("Show Configuration")
ticket_prefix = st.sidebar.text_input("Ticket Prefix (e.g., LEAR)", "LEAR").strip()

first_of_month = datetime.today().replace(day=1).date()
start_date = st.sidebar.date_input("Start Date", first_of_month)

sort_col = st.sidebar.selectbox("Sort By", ["Name", "Date", "Ticket ID"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password")
is_admin = (password_input == ADMIN_PASSWORD)

if st.sidebar.button("Refresh Manifest"):
    st.cache_data.clear()
    st.rerun()

# Run Core Pipeline Engine
df_spreadsheet = get_spreadsheet_transactions(conn)

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

if not df_combined.empty and ticket_prefix:
    prefix_lower = ticket_prefix.lower()
    df_combined = df_combined[
        df_combined['item_name'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['raw_checkout_val'].astype(str).str.lower().str.contains(prefix_lower) |
        df_combined['item id'].astype(str).str.lower().str.contains(prefix_lower)
    ]

# --- TRANSFORMATION & MATRIX GENERATION ENGINE ---
if not df_combined.empty:
    df = df_combined.copy()
    
    first_item_name = df['item_name'].iloc[0]
    if "Tickets" in str(first_item_name):
        show_title = str(first_item_name).replace(" Tickets", "").upper()
    else:
        show_title = str(ticket_prefix).upper()

    def extract_manifest_details(row):
        raw_show_date = str(row['raw_checkout_val']).strip()
        
        parts = [p.strip() for p in raw_show_date.split(',')]
        if len(parts) >= 2:
            show_date = f"{parts[0]}, {parts[1]}"
        else:
            show_date = raw_show_date
            
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
        
        grouped_df = display_df.groupby(['Last Name', 'name', 'Show Date'], as_index=False)['quantity'].sum()
        
        full_date_map = df.groupby('name')['Show Date'].unique().to_dict()

        def format_conditional_labels(row):
            patron_name = row['name']
            current_date = row['Show Date']
            
            all_patron_nights = list(full_date_map.get(patron_name, []))
            all_patron_nights.sort(key=lambda x: pd.to_datetime(x, errors='coerce'))
            
            total_nights_attended = len(all_patron_nights)
            
            if total_nights_attended <= 1:
                return patron_name
            else:
                night_index = all_patron_nights.index(current_date) + 1
                return f"{patron_name} [Night {night_index}/{total_nights_attended}]"
                
        grouped_df['Custom Label'] = grouped_df.apply(format_conditional_labels, axis=1)
        grouped_df['Parsed Performance Date'] = pd.to_datetime(grouped_df['Show Date'], errors='coerce')
        
        manifest = grouped_df.pivot_table(
            index=['Last Name', 'name', 'Custom Label', 'Parsed Performance Date', 'Show Date'], 
            columns='Show Date', 
            values='quantity', 
            aggfunc='sum'
        ).reset_index()
        
        manifest = manifest.sort_values(by=['Last Name', 'name', 'Parsed Performance Date'])
        manifest = manifest.rename(columns={'Custom Label': 'Purchaser Name'}).drop(columns=['Last Name', 'name', 'Parsed Performance Date', 'Show Date'])
        
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
                .astype(float)
                .astype(int)
                .astype(str)
                .replace('0', '-')
            )
        
        st.info("Check boxes to mark arrivals and click Save.")
        
        config = {
            "Checked In": st.column_config.CheckboxColumn("Arrived", width="small"),
            "Purchaser Name": st.column_config.Column("Purchaser Name", width=280, disabled=True)
        }
        for col in date_cols:
            config[col] = st.column_config.Column(col, width=120, alignment="center", disabled=True)
            
        edited_df = st.data_editor(manifest, column_config=config, hide_index=True, key="manifest_editor")
        
        if st.button("Save Changes to Google Sheet"):
            with st.spinner("Updating records..."):
                checkin_list = edited_df[(edited_df['Checked In'] == True) & (edited_df['Purchaser Name'] != "TOTAL TICKETS SOLD")][['Purchaser Name']]
                checkin_list.columns = ['Name']
                checkin_list['Status'] = 'Checked In'
                
                conn.update(worksheet="CheckIns", data=checkin_list)
                st.cache_data.clear() 
                st.success("Check-ins synced successfully!")
                st.rerun()
else:
    st.warning(f"No transactions found matching the prefix '{ticket_prefix}' in this timeline window.")
