import requests
import csv
import argparse
import sys
from datetime import datetime, timedelta, timezone
import streamlit as st
import pandas as pd

CLIENT_ID = st.secrets['CLIENT_ID']
CLIENT_SECRET = st.secrets['CLIENT_SECRET']
ADMIN_PASSWORD = st.secrets.get('ADMIN_PASSWORD', 'mtpockets123')

def get_access_token():
    url = f"https://api-m.paypal.com/v1/oauth2/token"
    headers = {'Accept': 'application/json', 'Accept-Language': 'en_US'}
    data = {'grant_type': 'client_credentials'}
    response = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), headers=headers, data=data)
    response.raise_for_status()
    return response.json()['access_token']

def search_transactions(prefix, start_date_str):
    token = get_access_token()
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

    try:
        current_start = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"Error: Date format must be YYYY-MM-DD (Received: {start_date_str})")
        sys.exit(1)

    ultimate_end = datetime.now(timezone.utc)
    all_rows = []

    print(f"Searching for prefix '{prefix}' starting from {start_date_str}...")

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

                # Unique transaction ID
                tx_id = t_info.get('transaction_id')

                # Parse Date and Time
                raw_time = t_info.get('transaction_initiation_date')
                if raw_time:
                    dt_obj = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                    f_date = dt_obj.strftime('%m-%d-%Y')
                    f_time = dt_obj.strftime('%H:%M:%S')
                else:
                    f_date, f_time = "N/A", "N/A"

                # Financials
                overall_fee = float(t_info.get('fee_amount', {}).get('value', 0))
                if overall_fee > 0: overall_fee = -overall_fee

                # Track if first item has already been processed
                first_item_in_tx = True

                # Transaction Type Identification
                # Tries the subject first, then falls back to the event code description
                tx_type = t_info.get('transaction_subject')
                if not tx_type:
                    # 'T0000' is a standard Website Payment code in PayPal
                    event_code = t_info.get('transaction_event_code', '')
                    tx_type = "Website Payment" if event_code.startswith('T00') else "General Payment"

                for item in items:
                    i_code = item.get('item_code', '')
                    i_name = item.get('item_name', '')

                    if (i_code and i_code.startswith(prefix)) or \
                       (i_name and i_name.startswith(prefix)):

                        # Use item_amount for the gross of the current row
                        row_gross = float(item.get('item_amount', {}).get('value',0))

                        # Apply the fee ONLY if this is the first item in the transaction
                        row_fee = overall_fee if first_item_in_tx else 0.0
                        row_net = row_gross + row_fee

                        # Set first item to false in case further items in the same transaction
                        first_item_in_tx = False

                        # Note: Dictionary keys match the final column order
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
                            'raw_checkout_val': item.get('checkout_options',
                                                         [{}])[0].get('checkout_option_value', 'N/A'),
                        })

        current_start = current_end + timedelta(seconds=1)

    return all_rows

st.set_page_config(page_title="MT Pockets Theatre", layout="wide")
st.title("MT Pockets Ticket Search")

# Sidebar for inputs
prefix = st.sidebar.text_input("Ticket Prefix", "CP")
start_date = st.sidebar.date_input("Start Date", datetime(2026,1,1))

# Sidebar for sorting options
sort_col = st.sidebar.selectbox("Sort By",["Date", "Name", "Ticket ID"])
sort_order = st.sidebar.radio("Order", ["Ascending", "Descending"])

# Access control
st.sidebar.header("Access Control")
password_input = st.sidebar.text_input("Admin Password (Optional)", type="password", help="(Optional: Enter password to unlock financial data and downloads.")
is_admin = (password_input == ADMIN_PASSWORD)

if st.button("Search Tickets"):
    with st.spinner("Talking to PayPal..."):
        results = search_transactions(prefix, start_date.strftime('%Y-%m-%d'))

        if results:
            df = pd.DataFrame(results)
            first_item_name = df['item_name'].iloc[0]
            show_title = first_item_name.replace(" Tickets", "").upper()

            def extract_manifest_details(row):
                #Show date
                raw_show_date = str(row['raw_checkout_val'])
                show_date = raw_show_date[:raw_show_date.rfind(",")] if "," in raw_show_date else raw_show_date

                # Last name
                full_name = str(row['name'].strip())
                last_name = full_name.split()[-1] if " " in full_name else full_name

                return pd.Series([show_date, last_name])

            # Add colums to dataframe
            df[['Show Date', 'Last Name']] = df.apply(extract_manifest_details, axis=1)

            # Convert financials to numbers
            df['gross'] = pd.to_numeric(df['gross'], errors='coerce')
            df['fee'] = pd.to_numeric(df['fee'], errors='coerce')
            df['net'] = pd.to_numeric(df['net'], errors='coerce')

            # Convert date to temporary datetime object for accurate sorting
            df['temp_date'] = pd.to_datetime(df['date'], format='%m-%d-%Y')

            # Sorting Logic
            ascending = (sort_order == "Ascending")

            if sort_col == "Date":
                df = df.sort_values(by=['temp_date', 'time'], ascending=ascending)
            elif sort_col == "Name":
                df = df.sort_values(by='name', ascending=ascending)
            elif sort_col == "Ticket ID":
                df = df.sort_values(by='item id', ascending=ascending)

            # Remove hidden datetime sorter helper
            df = df.drop(columns=['temp_date'])

            if is_admin:
                st.success(f"Admin access: Found {len(results)} transactions for {show_title}.")
                df = df.drop(columns=['raw_checkout_val', 'Last Name', 'Show Date', 'item_name'])
                admin_df = df.copy()

                # Convert quantities to integers
                admin_df['quantity'] = admin_df['quantity'].astype(int)

                # Format numbers for display
                for col in ['gross', 'fee', 'net']:
                    admin_df[col] = admin_df[col].map('${:,.2f}'.format)

                st.dataframe(admin_df,
                             width='stretch',
                             hide_index=True)

                # CSV Download
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV", csv, f"{show_title}_ticket_report.csv", "text/csv")

            else:
                st.success(f"Public access: Found {len(results)} transactions. Sorted by last name.")
                st.markdown(f'### {show_title}')

                # Create pivot table
                manifest = df.pivot_table(
                    index=['Last Name', 'name'],
                    columns='Show Date',
                    values='quantity',
                    aggfunc='sum'
                ).reset_index()

                # Sort by last name and clean up
                manifest = manifest.sort_values(by='Last Name').drop(columns=['Last Name'])
                manifest = manifest.rename(columns={'name': 'Purchaser Name'})

                # Keep list of date columns
                date_columns = [col for col in manifest.columns if col != 'Purchaser Name']

                # Calculate ticket totals
                totals_data = {"Purchaser Name": "TOTAL TICKETS SOLD"}
                for col in date_columns:
                    totals_data[col] = manifest[col].sum()
                totals_df = pd.DataFrame([totals_data])
                manifest = pd.concat([manifest, totals_df], ignore_index=True)

                # Fill empty spots with "-" for readability
                manifest = manifest.fillna(0)

                # Convert values to integers and then replace 0 with '-'
                for col in date_columns:
                    manifest[col] = manifest[col].astype(int).astype(str).replace('0', '-')

                # Configure column width
                config = {
                    'Purchaser Name': st.column_config.Column(
                        'Purchaser Name'
                    )
                }

                # Add date columns to config
                for col in date_columns:
                    config[col] = st.column_config.Column(
                        col,
                        required=True,
                        alignment="center",
                        help=f"Tickets for {col}"
                    )

                def bold_totals(row):
                    return['font-weight: bold' if row['Purchaser Name'] == "TOTAL TICKETS SOLD" else '' for _ in row]

                styled_manifest = manifest.style.apply(bold_totals, axis=1)

                st.dataframe(styled_manifest,
                             width='content',
                             hide_index=True,
                             column_config=config)
                st.warning("Password required to view financial data or download. "
                           "Please see admin for password if needed.")

        else:
            st.warning("No transactions found.")
