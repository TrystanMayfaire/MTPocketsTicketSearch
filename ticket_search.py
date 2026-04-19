import requests
import csv
import argparse
import sys
from datetime import datetime, timedelta, timezone
import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection

# 1. MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(page_title="MT Pockets Theatre", layout="wide")

# 2. LOAD SECRETS
CLIENT_ID = st.secrets['CLIENT_ID']
CLIENT_SECRET = st.secrets['CLIENT_SECRET']
ADMIN_PASSWORD = st.secrets.get('ADMIN_PASSWORD', 'mtpockets123')

# 3. INITIALIZE CONNECTION (Global)
conn = st.connection("gsheets", type=GSheetsConnection)

# --- PAYPAL FUNCTIONS ---
def get_access_token():
    url = f"https://api-m.paypal.com/v1/oauth2/token"
    headers = {'Accept': 'application/json', 'Accept-Language': 'en_US'}
    data = {'grant_type': 'client_credentials'}
    response = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), headers=headers, data=data)
    response.raise_for_status()
    return response.json()['access_token']

@st.cache_data(ttl=0)
def get_existing_checkins(_conn):
    return _conn.read(worksheet="CheckIns")

@st.cache_data(ttl=600) # Cache for 10 minutes (600 seconds)
def search_transactions(prefix, start_date_str):
    token = get_access_token()
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

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

        response = requests.get(f"https://api-m.paypal.com/v1/reporting/transactions", headers=headers, params=params)

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

                tx_type = t_info.get('transaction_subject')
                if not tx_type:
                    event_code = t_info.get('transaction_event_code', '')
                    tx_type = "Website Payment" if event_code.startswith('T00') else "General Payment"

                for item in items:
                    i_code = item.get('item_code', '')
                    i_name = item.get('item_name', '')

                    if (i_code and i_code.startswith(prefix)) or (i_name and i_name.startswith(prefix)):
                        row_gross = float(item.get('item_amount', {}).get('value',0))
                        row_fee = overall_fee if first_item_in_tx else 0.0
                        row_net = row_gross + row_fee
                        first_item_in_tx = False

                        all_rows.append({
                            'date': f_date,
                            'time': f_time,
                            'name': p_info.get('payer_name', {}).get('alternate_full_name', 'N/A'),
                            'transaction type': tx_type,
                            'gross': f"{row_gross:.2f}",
                            'fee': f"{row_fee:.2f}",
                            'net': f"{row_net:.2f}",
                            'email address': p_info.get('email_address', 'N/A'),
                            'item id': i_code or i_name,
                            'quantity': int(item.get('item_quantity', '1')),
                            'item_name': item.get('item_name', 'Current Show'),
                            'raw_checkout_val': item.get('checkout_options', [{}])[0].get('checkout_option_value', 'N/A'),
                        })
        current_start = current_end + timedelta(seconds=1)
    return all_rows

# --- MAIN UI ---
st.title("MT Pockets Ticket Search")

prefix = st.sidebar.text_input("Ticket Prefix", "CP")
start_date = st.sidebar.date_input("Start Date", datetime(2026,1,1))
sort_col = st.sidebar.selectbox("Sort By",["Date", "Name", "Ticket ID"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password")
is_admin = (password_input == ADMIN_PASSWORD)

if "results" not in st.session_state:
    st.session_state.results = None

if st.button("Search Tickets"):
    st.cache_data.clear()
    with st.spinner("Syncing PayPal and Check-in data..."):
        st.session_state.results = search_transactions(prefix, start_date.strftime('%Y-%m-%d'))

if st.session_state.results:
    results = st.session_state.results
    df = pd.DataFrame(results)
    first_item_name = df['item_name'].iloc[0]
    show_title = first_item_name.replace(" Tickets", "").upper()

    def extract_manifest_details(row):
        raw_show_date = str(row['raw_checkout_val'])
        show_date = raw_show_date[:raw_show_date.rfind(",")] if "," in raw_show_date else raw_show_date
        full_name = str(row['name'].strip())
        last_name = full_name.split()[-1] if " " in full_name else full_name
        return pd.Series([show_date, last_name])

    df[['Show Date', 'Last Name']] = df.apply(extract_manifest_details, axis=1)
    df['gross'] = pd.to_numeric(df['gross'], errors='coerce')
    df['fee'] = pd.to_numeric(df['fee'], errors='coerce')
    df['net'] = pd.to_numeric(df['net'], errors='coerce')
    df['temp_date'] = pd.to_datetime(df['date'], format='%m-%d-%Y')

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
        
        manifest = display_df.pivot_table(index=['Last Name', 'name'], columns='Show Date', values='quantity', aggfunc='sum').reset_index()
        manifest = manifest.sort_values(by='Last Name').rename(columns={'name': 'Purchaser Name'}).drop(columns=['Last Name'])
        date_cols = [c for c in manifest.columns if c != 'Purchaser Name']
        
        # Try to sort dates naturally (e.g., Apr 18 before Apr 19)
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
            lambda row: f"✅ {row['Purchaser Name']}" if row['Checked In'] == True and row['Purchaser Name'] != "TOTAL TICKETS SOLD" else row['Purchaser Name'], 
            axis=1
        )
        
        # Row Highlighting
        def highlight_checked_in(row):
            if row['Checked In'] and row['Purchaser Name'] != "TOTAL TICKETS SOLD":
                return ['background-color: #d4edda'] * len(row) # Light green
            elif row['Purchaser Name'] == "TOTAL TICKETS SOLD":
                return ['background-color: #f8f9fa; font-weight: bold'] * len(row) # Light grey
            return [''] * len(row)
        
        # --- FORMATTING ---
        for col in date_cols:
            manifest[col] = (
                manifest[col]
                .fillna(0)
                .astype(float)
                .astype(int)
                .astype(str)
                .replace('0', '-')
            )
        
        # --- DISPLAY & SAVE ---
        st.info("Check boxes to mark arrivals and click Save.")
        
        config = {
            "Checked In": st.column_config.CheckboxColumn("Arrived", width="small"),
            "Purchaser Name": st.column_config.Column("Purchaser Name", width=250, disabled=True)
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
                
                # Clear cache so next rerun pulls fresh Google Sheet data
                st.cache_data.clear() 
                st.success("Check-ins synced successfully!")
                st.rerun()
                
                st.success("Check-ins synced successfully!")
                
                # Rerun the app to refresh the UI with the saved data
                st.rerun()
    else:
        st.success(f"Admin access for {show_title}.")
        admin_display = df.drop(columns=['raw_checkout_val', 'Last Name', 'Show Date', 'item_name']).copy()
        for col in ['gross', 'fee', 'net']:
            admin_display[col] = admin_display[col].map('${:,.2f}'.format)
        st.dataframe(admin_display, hide_index=True)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", csv, f"{show_title}_report.csv")
