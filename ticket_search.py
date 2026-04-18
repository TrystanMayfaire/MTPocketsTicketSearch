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

if st.button("Search Tickets"):
    with st.spinner("Syncing PayPal and Check-in data..."):
        results = search_transactions(prefix, start_date.strftime('%Y-%m-%d'))

        if results:
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

            if is_admin:
                st.success(f"Admin access for {show_title}.")
                admin_display = df.drop(columns=['raw_checkout_val', 'Last Name', 'Show Date', 'item_name']).copy()
                for col in ['gross', 'fee', 'net']:
                    admin_display[col] = admin_display[col].map('${:,.2f}'.format)
                st.dataframe(admin_display, hide_index=True)
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV", csv, f"{show_title}_report.csv")

            else:
                st.markdown(f'### {show_title}')
                manifest = df.pivot_table(index=['Last Name', 'name'], columns='Show Date', values='quantity', aggfunc='sum').reset_index()
                manifest = manifest.sort_values(by='Last Name').rename(columns={'name': 'Purchaser Name'}).drop(columns=['Last Name'])
                date_columns = [col for col in manifest.columns if col != 'Purchaser Name']
                
                # --- GOOGLE SHEETS MERGE ---
                try:
                    existing_checkins = get_existing_checkins(conn)
                    manifest['Checked In'] = manifest['Purchaser Name'].isin(existing_checkins['Name'].tolist())
                except:
                    manifest['Checked In'] = False

                cols = ['Checked In'] + [c for c in manifest.columns if c != 'Checked In']
                manifest = manifest[cols]

                # --- TOTALS ---
                manifest_numeric = manifest.fillna(0)
                totals_data = {"Purchaser Name": "TOTAL TICKETS SOLD", "Checked In": False}
                for col in date_columns:
                    totals_data[col] = manifest_numeric[col].sum()
                manifest = pd.concat([manifest, pd.DataFrame([totals_data])], ignore_index=True)

                # --- FORMATTING ---
                manifest = manifest.fillna(0)
                for col in date_columns:
                    manifest[col] = manifest[col].astype(float).astype(int).astype(str).replace('0', '-')

                # --- DISPLAY & SAVE ---
                st.info("Check boxes to mark arrivals and click Save.")
                
                config = {
                    "Checked In": st.column_config.CheckboxColumn("Arrived", width="small"),
                    "Purchaser Name": st.column_config.Column("Purchaser Name", width=250, disabled=True)
                }
                for col in date_columns:
                    config[col] = st.column_config.Column(col, width=120, alignment="center", disabled=True)

                edited_df = st.data_editor(manifest, column_config=config, hide_index=True, key="manifest_editor")

                if st.button("Save Changes to Google Sheet"):
                    with st.spinner("Updating check-in records..."):
                        # Filter only those who are checked in, excluding the Totals row
                        checkin_list = edited_df[
                            (edited_df['Checked In'] == True) & 
                            (edited_df['Purchaser Name'] != "TOTAL TICKETS SOLD")
                        ][['Purchaser Name']]
                        
                        # Format for the Google Sheet
                        checkin_list.columns = ['Name']
                        checkin_list['Status'] = 'Checked In'
                        
                        # Update Google Sheets
                        conn.update(worksheet="CheckIns", data=checkin_list)
                        
                        # IMPORTANT: Clear the cache so the app pulls the FRESH 
                        # list from the Google Sheet on the next run
                        st.cache_data.clear() 
                        
                        st.success("Check-ins synced successfully!")
                        
                        # Rerun the app to refresh the UI with the saved data
                        st.rerun()
