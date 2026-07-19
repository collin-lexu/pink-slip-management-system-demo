from flask import Flask, request, Response, redirect
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from decimal import Decimal
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

class PinkSlip(db.Model):
    __tablename__ = 'pink_slip'

    id = db.Column(db.Integer, primary_key=True)
    slip_number = db.Column(db.String(6), unique=True, nullable=False)
    first_initial = db.Column(db.String(1), nullable=False)
    last_name = db.Column(db.String(30), nullable=False)
    phone = db.Column(db.String(16), nullable=False)
    date_received = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False) # generally 14 day turn around unless rush fee is applied
    due_time = db.Column(db.String(8), nullable=True) # due times usually range between 10am-6pm
    rush_fee = db.Column(db.Numeric(10, 2), default=Decimal('0.00'), nullable=False) # rush fee total is calculated and inputted manually if applicable, otherwise it is $0.00
    total_amount = db.Column(db.Numeric(10, 2), default=Decimal('0.00'), nullable=False)

    items = db.relationship(
        'PinkSlipItem',
        backref='slip',
        cascade='all, delete-orphan',
        lazy='select'
    )

class PinkSlipItem(db.Model):
    __tablename__ = 'pink_slip_item'

    id = db.Column(db.Integer, primary_key=True)
    slip_id = db.Column(db.Integer, db.ForeignKey('pink_slip.id'), nullable=False)
    item_number = db.Column(db.Integer, nullable=True) # position of this item on its slip; distinguishes otherwise-identical items so imports don't misdetect them as duplicates
    item_type = db.Column(db.String(10), nullable=False)
    work_description = db.Column(db.String(100)) # if the item type is 'Other', the work description should include both the actual item and the work to be done
    price = db.Column(db.Numeric(10, 2), nullable=False)

def _format_date_val(val):
    if not val or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == '':
        return None
    if isinstance(val, pd.Timestamp):
        return val.date()
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None

def _parse_time(val):
    if not val or pd.isna(val) or str(val).strip() == '':
        return ''
    if isinstance(val, pd.Timestamp):
        if val.time().hour == 0 and val.time().minute == 0:
            return ''
        return val.strftime('%I:%M %p').lstrip('0')
    try:
        parsed = pd.to_datetime(str(val).strip())
        if parsed.time().hour == 0 and parsed.time().minute == 0:
            return ''
        return parsed.strftime('%I:%M %p').lstrip('0')
    except Exception:
        return ''

def _time_to_input_value(due_time_str):
    if not due_time_str:
        return ''
    try:
        return pd.to_datetime(due_time_str).strftime('%H:%M')
    except Exception:
        return ''

def _format_phone(phone_str):
    if not phone_str or pd.isna(phone_str):
        return ''
    digits = ''.join(c for c in str(phone_str) if c.isdigit())
    if not digits:
        return ''
    if len(digits) == 7:  # default to 704 area code if given 7 digit number considering some old-school Charlotte natives prefer this way 
        digits = '704' + digits
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return str(phone_str).strip()

def _parse_rush_fee(val):
    if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == '':
        return Decimal('0.00')
    cleaned = str(val).strip().replace('$', '').replace(',', '')
    try:
        fee = Decimal(cleaned)
        if fee < 0:
            return Decimal('0.00')
        return fee
    except Exception:
        return Decimal('0.00')

def _recalculate_total(pink_slip):
    pink_slip.total_amount = Decimal(str(sum(it.price for it in pink_slip.items))) + pink_slip.rush_fee

VALID_ITEM_TYPES = ['Shirt', 'Jeans', 'Dress', 'Jacket', 'Coat', 'Pants', 'Skirt', 'Shorts', 'Other']

ITEM_TYPE_ALIASES = {
    'shirts': 'Shirt', 'tshirt': 'Shirt', 't-shirt': 'Shirt', 'tee': 'Shirt', 'blouse': 'Shirt', 'top': 'Shirt',
    'jean': 'Jeans', 'denim': 'Jeans',
    'dresses': 'Dress', 'gown': 'Dress',
    'jackets': 'Jacket', 'blazer': 'Jacket',
    'coats': 'Coat', 'overcoat': 'Coat',
    'pant': 'Pants', 'trousers': 'Pants', 'slacks': 'Pants',
    'skirts': 'Skirt',
    'short': 'Shorts',
    'misc': 'Other', 'miscellaneous': 'Other', 'etc': 'Other',
}

def _normalize_item_type(item_type_str):
    if not item_type_str or pd.isna(item_type_str):
        return None, False

    item_type_str = str(item_type_str).strip()
    if not item_type_str:
        return None, False

    for valid_type in VALID_ITEM_TYPES:
        if item_type_str.lower() == valid_type.lower():
            return valid_type, True

    lower_input = item_type_str.lower()
    if lower_input in ITEM_TYPE_ALIASES:
        return ITEM_TYPE_ALIASES[lower_input], True

    for valid_type in VALID_ITEM_TYPES:
        if lower_input.startswith(valid_type.lower()) or valid_type.lower() in lower_input:
            return valid_type, True

    return None, False

NAV_HTML = """
<nav class="site-nav">
    <span class="brand">Pink Slip Management System</span>
    <a href="/">Home</a>
    <a href="/add_pink_slip">Add Pink Slip</a>
    <a href="/records">View Records</a>
    <a href="/export">Export CSV</a>
</nav>
"""

PAGE_END = """
    </div>
</body>
</html>
"""

def _page_start(title):
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
{NAV_HTML}
    <div class="container">
"""

def _message_page(title, message, link_url="/", link_text="Back to Home", status=200):
    html = _page_start(title) + f"""
    <h1>{title}</h1>
    <div class="message-box">
        <p>{message}</p>
        <a href="{link_url}"><button type="button">{link_text}</button></a>
    </div>
    """ + PAGE_END
    return html, status

@app.route("/")
def home():
    return _page_start("Pink Slip Management System") + """
    <div class="upload-box">
        <form method="POST" action="/upload" enctype="multipart/form-data">
            <input type="file" name="file">
            <input type="submit" value="Upload CSV/Excel">
        </form>
    </div>
    <div class="actions">
        <a href="/add_pink_slip"><button type="button">Add A Pink Slip</button></a>
        <a href="/records"><button type="button">View All Records</button></a>
        <a href="/export"><button type="button">Export CSV</button></a>
    </div>
    """ + PAGE_END

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get('file')
    if not file:
        return _page_start("Upload Error") + """
        <h1>Error: No File Uploaded</h1>
        <div class="message-box">
            <p>Please select a file before uploading.</p>
            <a href="/"><button type="button">Back to Upload</button></a>
        </div>
        """ + PAGE_END, 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)

    if file.filename.endswith('.csv'):
        df = pd.read_csv(filepath, dtype=str).fillna('')
    elif file.filename.endswith(('.xls', '.xlsx')):
        df = pd.read_excel(filepath, dtype=str).fillna('')
    else:
        return _page_start("Upload Error") + """
        <h1>Unsupported File Type</h1>
        <div class="message-box">
            <p>Please upload a .csv, .xls, or .xlsx file.</p>
            <a href="/"><button type="button">Back to Upload</button></a>
        </div>
        """ + PAGE_END, 400

    slips_created = 0
    items_imported = 0
    duplicates_skipped = 0
    rows_skipped = 0
    error_rows = []
    slips_cache = {}

    for idx, row in df.iterrows():
        row_number = idx + 2  # account for header row

        slip_number = str(row.get('slip_number', '')).strip()
        first_initial = str(row.get('first_initial', '')).strip().upper()[:1]
        last_name = str(row.get('last_name', '')).strip()
        phone = _format_phone(row.get('phone', ''))
        item_type_raw = str(row.get('item_type', '')).strip()
        work_description = str(row.get('work_description', '')).strip()
        price_raw = str(row.get('price', '')).strip().replace('$', '').replace(',', '')
        item_number_raw = str(row.get('item_number', '')).strip()
        date_received_raw = row.get('date_received', '')
        due_date_raw = row.get('due_date', '')
        due_time_raw = row.get('due_time', '')
        rush_fee_raw = row.get('rush_fee', '')

        if not slip_number:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": "",
                "error": "Missing slip_number"
            })
            continue

        item_type, item_type_valid = _normalize_item_type(item_type_raw)
        if not item_type_valid:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": f"Invalid item_type: '{item_type_raw}'. Valid types: {', '.join(VALID_ITEM_TYPES)}"
            })
            continue

        try:
            price = Decimal(price_raw)
        except Exception:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": "Invalid price format"
            })
            continue

        if price < 0:
            rows_skipped += 1
            error_rows.append({
                "row": row_number,
                "slip_number": slip_number,
                "error": "Negative price not allowed"
            })
            continue

        try:
            item_number = int(item_number_raw) if item_number_raw else None
        except ValueError:
            item_number = None

        date_received = _format_date_val(date_received_raw)
        due_date = _format_date_val(due_date_raw)
        if due_time_raw and str(due_time_raw).strip():
            due_time = _parse_time(due_time_raw)
        else:
            due_time = _parse_time(due_date_raw)

        pink_slip = slips_cache.get(slip_number)
        if pink_slip is None:
            pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
            if pink_slip is None:
                pink_slip = PinkSlip(
                    slip_number=slip_number,
                    first_initial=first_initial or '?',
                    last_name=last_name or 'Unknown',
                    phone=phone,
                    date_received=date_received,
                    due_date=due_date,
                    due_time=due_time,
                    rush_fee=_parse_rush_fee(rush_fee_raw),
                    total_amount=0.0
                )
                db.session.add(pink_slip)
                slips_created += 1
            else:
                # fill in any missing fields from the CSV
                if first_initial and not pink_slip.first_initial:
                    pink_slip.first_initial = first_initial
                if last_name and not pink_slip.last_name:
                    pink_slip.last_name = last_name
                if phone and not pink_slip.phone:
                    pink_slip.phone = phone
                if date_received and not pink_slip.date_received:
                    pink_slip.date_received = date_received
                if due_date and not pink_slip.due_date:
                    pink_slip.due_date = due_date
                if due_time and not pink_slip.due_time:
                    pink_slip.due_time = due_time
                if rush_fee_raw and pink_slip.rush_fee == 0:
                    pink_slip.rush_fee = _parse_rush_fee(rush_fee_raw)
            slips_cache[slip_number] = pink_slip

        # skip duplicate items on the same slip
        duplicate_found = False
        for existing_item in pink_slip.items:
            if (existing_item.item_number == item_number and
                existing_item.item_type == item_type and
                (existing_item.work_description or '') == (work_description or '') and
                float(existing_item.price) == float(price)):
                duplicate_found = True
                break

        if duplicate_found:
            duplicates_skipped += 1
            continue

        item = PinkSlipItem(
            slip=pink_slip,
            item_number=item_number,
            item_type=item_type,
            work_description=work_description,
            price=price
        )
        db.session.add(item)
        items_imported += 1

    # recalculate totals
    for pink_slip in slips_cache.values():
        _recalculate_total(pink_slip)
        db.session.add(pink_slip)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _page_start("Upload Error") + """
        <h1>Import Failed</h1>
        <div class="message-box">
            <p>Database integrity error during import. No changes were committed.</p>
            <a href="/"><button type="button">Back to Upload</button></a>
        </div>
        """ + PAGE_END, 500

    html = _page_start("Upload Results")
    html += "<h1>Upload Results</h1>"
    html += '<div class="actions">'
    html += '<a href="/"><button type="button">Upload Another File</button></a> '
    html += '<a href="/records"><button type="button">View All Records</button></a>'
    html += '</div>'
    html += (
        f"<p><b>Import complete:</b> {slips_created} slips created, "
        f"{items_imported} items imported, {duplicates_skipped} duplicate items skipped, "
        f"{rows_skipped} rows rejected.</p>"
    )

    if error_rows:
        html += "<h3>Rejected Rows</h3>"
        html += "<table>"
        html += "<tr><th>Row</th><th>Slip Number</th><th>Error</th></tr>"
        for err in error_rows:
            html += (
                f"<tr>"
                f"<td>{err['row']}</td>"
                f"<td>{err['slip_number']}</td>"
                f"<td>{err['error']}</td>"
                f"</tr>"
            )
        html += "</table>"

    html += PAGE_END
    return html

@app.route("/records")
def records():
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 25

    query = PinkSlip.query

    if search_query:
        search_filter = (
            PinkSlip.slip_number.ilike(f'%{search_query}%') |
            PinkSlip.first_initial.ilike(f'%{search_query}%') |
            PinkSlip.last_name.ilike(f'%{search_query}%') |
            PinkSlip.phone.ilike(f'%{search_query}%')
        )

        # also match phone numbers regardless of how they're punctuated, e.g.
        # "980-898-2022" or "9808982022" against the stored "(980) 898-2022"
        digits_only = ''.join(c for c in search_query if c.isdigit())
        if digits_only:
            normalized_phone = PinkSlip.phone
            for char in ('(', ')', '-', ' '):
                normalized_phone = db.func.replace(normalized_phone, char, '')
            search_filter = search_filter | normalized_phone.ilike(f'%{digits_only}%')

        query = query.filter(search_filter)

    pagination = query.order_by(PinkSlip.slip_number).paginate(page=page, per_page=per_page, error_out=False)
    slips = pagination.items

    html = _page_start("Records")
    html += "<h1>Records</h1>"
    html += '<div class="actions"><a href="/"><button type="button">Back to Upload</button></a></div>'

    html += '''
    <form method="GET" action="/records" class="search-bar">
        <input type="text" name="search" placeholder="Search by slip number, customer, or phone"
               value="{}" size="40">
        <input type="submit" value="Search">
        <a href="/records"><button type="button" class="secondary">Clear Search</button></a>
    </form>
    '''.format(search_query)

    if search_query:
        html += f"<p class=\"result-summary\">Showing results for: '{search_query}' ({pagination.total} slip(s) found)</p>"

    html += f"<p class=\"result-summary\">Showing {len(slips)} of {pagination.total} slips (Page {pagination.page} of {pagination.pages})</p>"

    if not slips:
        html += "<p>No records found.</p>"
        html += PAGE_END
        return html

    for t in slips:
        html += '<div class="slip-card">'
        html += '<div class="slip-card-header">'
        html += f"<h2>Slip: {t.slip_number} | Customer: {t.first_initial}. {t.last_name} | Phone: {t.phone}</h2>"
        html += '<div class="slip-actions">'
        html += f'<a href="/edit_pink_slip/{t.slip_number}"><button type="button" class="secondary">Edit</button></a>'
        html += '</div></div>'
        html += (
            f"<p class=\"meta\">Date Received: {t.date_received.strftime('%m/%d/%Y') if t.date_received else 'N/A'} | Due: {t.due_date.strftime('%m/%d/%Y') if t.due_date else 'N/A'}"
            f"{(' at ' + t.due_time) if t.due_time else ''} | Total: ${t.total_amount:.2f}</p>"
        )
        if t.rush_fee and t.rush_fee > 0:
            html += f'<p class="rush-fee">Rush Fee: ${t.rush_fee:.2f}</p>'
        html += "<ul>"
        for it in t.items:
            desc = f" - {it.work_description}" if it.work_description else ""
            html += f"<li>{it.item_type}{desc} - ${it.price:.2f}</li>"
        html += "</ul>"
        html += '</div>'

    search_param = f"&search={search_query}" if search_query else ""
    html += '<div class="pagination">'
    if pagination.has_prev:
        html += f'<a href="/records?page={pagination.prev_num}{search_param}">&laquo; Previous</a>'
    for p in pagination.iter_pages(left_edge=1, right_edge=1, left_current=2, right_current=2):
        if p is None:
            html += '<span>&hellip;</span>'
        elif p == pagination.page:
            html += f'<b>{p}</b>'
        else:
            html += f'<a href="/records?page={p}{search_param}">{p}</a>'
    if pagination.has_next:
        html += f'<a href="/records?page={pagination.next_num}{search_param}">Next &raquo;</a>'
    html += '</div>'

    html += PAGE_END
    return html

@app.route("/add_pink_slip", methods=["GET", "POST"])
def add_pink_slip():
    if request.method == "POST":
        slip_number = request.form.get("slip_number", "").strip()
        first_initial = request.form.get("first_initial", "").strip().upper()[:1]
        last_name = request.form.get("last_name", "").strip()
        phone = _format_phone(request.form.get("phone", ""))
        date_received = _format_date_val(request.form.get("date_received", ""))
        due_date = _format_date_val(request.form.get("due_date", ""))
        due_time = _parse_time(request.form.get("due_time", ""))
        rush_fee_raw = request.form.get("rush_fee", "").strip()
        rush_fee_clean = rush_fee_raw.replace('$', '').replace(',', '')
        if rush_fee_clean:
            try:
                rush_fee = Decimal(rush_fee_clean)
                if rush_fee < 0:
                    raise ValueError
            except Exception:
                return _message_page("Invalid Rush Fee", "Invalid rush fee. Must be a positive number.",
                                      "/add_pink_slip", "Back to Form", 400)
        else:
            rush_fee = Decimal('0.00')

        item_types_raw = request.form.getlist("item_type")
        work_descriptions = request.form.getlist("work_description")
        prices_raw = request.form.getlist("price")

        if not item_types_raw:
            return _message_page("Missing Items", "At least one item is required.",
                                  "/add_pink_slip", "Back to Form", 400)

        validated_items = []
        for i, (it_raw, wd, pr) in enumerate(zip(item_types_raw, work_descriptions, prices_raw), start=1):
            item_type, item_type_valid = _normalize_item_type(it_raw.strip())
            if not item_type_valid:
                return _message_page("Invalid Item Type",
                                      f"Item {i}: Invalid item type. Valid options: {', '.join(VALID_ITEM_TYPES)}",
                                      "/add_pink_slip", "Back to Form", 400)
            price_clean = pr.strip().replace('$', '').replace(',', '')
            try:
                price = Decimal(price_clean)
                if price < 0:
                    raise ValueError
            except Exception:
                return _message_page("Invalid Price", f"Item {i}: Invalid price. Must be a positive number.",
                                      "/add_pink_slip", "Back to Form", 400)
            validated_items.append((item_type, wd.strip(), price))

        pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
        if not pink_slip:
            pink_slip = PinkSlip(
                slip_number=slip_number,
                first_initial=first_initial or '?',
                last_name=last_name or 'Unknown',
                phone=phone,
                date_received=date_received,
                due_date=due_date,
                due_time=due_time,
                rush_fee=rush_fee,
                total_amount=0.0
            )
            db.session.add(pink_slip)

        next_item_number = max((it.item_number or 0) for it in pink_slip.items) if pink_slip.items else 0
        for i, (item_type, work_description, price) in enumerate(validated_items, start=next_item_number + 1):
            item = PinkSlipItem(
                slip=pink_slip,
                item_number=i,
                item_type=item_type,
                work_description=work_description,
                price=price
            )
            db.session.add(item)

        _recalculate_total(pink_slip)
        db.session.commit()

        return _message_page("Pink Slip Added",
                              f"Pink slip {slip_number} added successfully with {len(validated_items)} item(s)!",
                              "/records", "View Records")

    item_options = ''.join(f'<option value="{t}">{t}</option>' for t in VALID_ITEM_TYPES)

    return _page_start("Add Pink Slip") + f"""
    <h1>Add Pink Slip</h1>
    <form method="POST">
        <fieldset>
            <legend>Slip Info</legend>
            Slip Number: <input type="text" name="slip_number" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" oninput="this.value=this.value.replace(/[^0-9]/g,'')" required><br>
            First Initial: <input type="text" name="first_initial" maxlength="1" pattern="[A-Za-z]" title="One letter only" oninput="this.value=this.value.replace(/[^A-Za-z]/g,'')" required><br>
            Last Name: <input type="text" name="last_name" pattern="[A-Za-z \\-']+" title="Letters, spaces, hyphens, and apostrophes only" oninput="this.value=this.value.replace(/[^A-Za-z \\-']/g,'')" required><br>
            Phone: <input type="text" name="phone" pattern="[0-9()\\- ]+" title="Numbers, parentheses, and dashes only" oninput="this.value=this.value.replace(/[^0-9()\\- ]/g,'')" required><br>
            Date Received: <input type="date" name="date_received" required><br>
            Due Date: <input type="date" name="due_date" required><br>
            Due Time: <input type="time" name="due_time"><br>
            Rush Fee: <input type="text" name="rush_fee" inputmode="decimal" placeholder="0.00" oninput="this.value=this.value.replace(/[^0-9.]/g,'')"><br>
        </fieldset>
        <fieldset>
            <legend>Items</legend>
            <div id="items-container">
                <div class="item-row">
                    Item Type: <select name="item_type" required>{item_options}</select>
                    Work Description: <input type="text" name="work_description">
                    Price: <input type="text" name="price" inputmode="decimal" oninput="this.value=this.value.replace(/[^0-9.]/g,'')" required>
                </div>
            </div>
            <br>
            <button type="button" onclick="addItem()">+ Add Another Item</button>
        </fieldset>
        <br>
        <input type="submit" value="Add Pink Slip">
    </form>
    <div class="actions">
        <a href="/"><button type="button" class="secondary">Back to Home</button></a>
    </div>
    <script>
    function addItem() {{
        var container = document.getElementById('items-container');
        var row = document.createElement('div');
        row.className = 'item-row';
        row.style.marginTop = '8px';
        row.innerHTML = 'Item Type: <select name="item_type" required>{item_options}</select> '
            + 'Work Description: <input type="text" name="work_description"> '
            + 'Price: <input type="text" name="price" inputmode="decimal" required> '
            + '<button type="button" onclick="this.parentElement.remove()">Remove</button>';
        var priceInput = row.querySelector('input[name="price"]');
        priceInput.addEventListener('input', function() {{
            this.value = this.value.replace(/[^0-9.]/g, '');
        }});
        container.appendChild(row);
    }}
    </script>
    """ + PAGE_END

@app.route("/delete_pink_slip/<slip_number>", methods=["POST"])
def delete_pink_slip(slip_number):
    pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
    if not pink_slip:
        return _message_page("Slip Not Found", f"No pink slip found with number {slip_number}.",
                              "/records", "Back to Records", 404)
    db.session.delete(pink_slip)
    db.session.commit()
    return redirect("/records")

@app.route("/delete_item/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    item = db.session.get(PinkSlipItem, item_id)
    if not item:
        return _message_page("Item Not Found", "No item found with that ID.",
                              "/records", "Back to Records", 404)
    pink_slip = item.slip
    slip_number = pink_slip.slip_number
    db.session.delete(item)
    _recalculate_total(pink_slip)
    db.session.commit()
    return redirect(f"/edit_pink_slip/{slip_number}")

@app.route("/edit_pink_slip/<slip_number>", methods=["GET", "POST"])
def edit_pink_slip(slip_number):
    pink_slip = PinkSlip.query.filter_by(slip_number=slip_number).first()
    if not pink_slip:
        return _message_page("Slip Not Found", f"No pink slip found with number {slip_number}.",
                              "/records", "Back to Records", 404)

    if request.method == "POST":
        first_initial = request.form.get("first_initial", "").strip().upper()[:1]
        last_name = request.form.get("last_name", "").strip()
        phone = _format_phone(request.form.get("phone", ""))
        date_received = _format_date_val(request.form.get("date_received", ""))
        due_date = _format_date_val(request.form.get("due_date", ""))
        due_time = _parse_time(request.form.get("due_time", ""))
        rush_fee_raw = request.form.get("rush_fee", "").strip()
        rush_fee_clean = rush_fee_raw.replace('$', '').replace(',', '')
        if rush_fee_clean:
            try:
                rush_fee = Decimal(rush_fee_clean)
                if rush_fee < 0:
                    raise ValueError
            except Exception:
                return _message_page("Invalid Rush Fee", "Invalid rush fee. Must be a positive number.",
                                      f"/edit_pink_slip/{slip_number}", "Back to Form", 400)
        else:
            rush_fee = Decimal('0.00')

        item_ids_raw = request.form.getlist("item_id")
        item_types_raw = request.form.getlist("item_type")
        work_descriptions = request.form.getlist("work_description")
        prices_raw = request.form.getlist("price")

        if not item_types_raw:
            return _message_page("Missing Items", "At least one item is required.",
                                  f"/edit_pink_slip/{slip_number}", "Back to Form", 400)

        # existing item rows carry their item_id; rows added via "+ Add Another Item" don't
        item_ids_padded = item_ids_raw + [''] * (len(item_types_raw) - len(item_ids_raw))

        validated_items = []
        for i, (item_id_raw, it_raw, wd, pr) in enumerate(
                zip(item_ids_padded, item_types_raw, work_descriptions, prices_raw), start=1):
            item_type, item_type_valid = _normalize_item_type(it_raw.strip())
            if not item_type_valid:
                return _message_page("Invalid Item Type",
                                      f"Item {i}: Invalid item type. Valid options: {', '.join(VALID_ITEM_TYPES)}",
                                      f"/edit_pink_slip/{slip_number}", "Back to Form", 400)
            price_clean = pr.strip().replace('$', '').replace(',', '')
            try:
                price = Decimal(price_clean)
                if price < 0:
                    raise ValueError
            except Exception:
                return _message_page("Invalid Price", f"Item {i}: Invalid price. Must be a positive number.",
                                      f"/edit_pink_slip/{slip_number}", "Back to Form", 400)
            validated_items.append((item_id_raw.strip(), item_type, wd.strip(), price))

        pink_slip.first_initial = first_initial or '?'
        pink_slip.last_name = last_name or 'Unknown'
        pink_slip.phone = phone
        pink_slip.date_received = date_received
        pink_slip.due_date = due_date
        pink_slip.due_time = due_time
        pink_slip.rush_fee = rush_fee

        existing_items_by_id = {str(it.id): it for it in pink_slip.items}
        next_item_number = max((it.item_number or 0) for it in pink_slip.items) if pink_slip.items else 0

        for item_id_raw, item_type, work_description, price in validated_items:
            if item_id_raw and item_id_raw in existing_items_by_id:
                item = existing_items_by_id[item_id_raw]
                item.item_type = item_type
                item.work_description = work_description
                item.price = price
            else:
                next_item_number += 1
                item = PinkSlipItem(
                    slip=pink_slip,
                    item_number=next_item_number,
                    item_type=item_type,
                    work_description=work_description,
                    price=price
                )
                db.session.add(item)

        _recalculate_total(pink_slip)
        db.session.commit()

        return _message_page("Pink Slip Updated",
                              f"Pink slip {slip_number} updated successfully.",
                              "/records", "View Records")

    def _item_options(selected):
        return ''.join(
            f'<option value="{t}"{" selected" if t == selected else ""}>{t}</option>' for t in VALID_ITEM_TYPES
        )

    item_rows_html = ""
    for it in pink_slip.items:
        item_rows_html += f"""
                <div class="item-row">
                    <input type="hidden" name="item_id" value="{it.id}">
                    Item Type: <select name="item_type" required>{_item_options(it.item_type)}</select>
                    Work Description: <input type="text" name="work_description" value="{it.work_description or ''}">
                    Price: <input type="text" name="price" inputmode="decimal" value="{it.price:.2f}" oninput="this.value=this.value.replace(/[^0-9.]/g,'')" required>
                    <button type="submit" class="danger small" formaction="/delete_item/{it.id}" formmethod="POST" formnovalidate
                        onclick="return confirm('Delete this item?');">Delete</button>
                </div>"""

    item_options_blank = _item_options(None)

    return _page_start(f"Edit Pink Slip {slip_number}") + f"""
    <h1>Edit Pink Slip {slip_number}</h1>
    <form method="POST">
        <fieldset>
            <legend>Slip Info</legend>
            Slip Number: <input type="text" value="{slip_number}" disabled><br>
            First Initial: <input type="text" name="first_initial" maxlength="1" pattern="[A-Za-z]" title="One letter only" value="{pink_slip.first_initial}" oninput="this.value=this.value.replace(/[^A-Za-z]/g,'')" required><br>
            Last Name: <input type="text" name="last_name" pattern="[A-Za-z \\-']+" title="Letters, spaces, hyphens, and apostrophes only" value="{pink_slip.last_name}" oninput="this.value=this.value.replace(/[^A-Za-z \\-']/g,'')" required><br>
            Phone: <input type="text" name="phone" pattern="[0-9()\\- ]+" title="Numbers, parentheses, and dashes only" value="{pink_slip.phone}" oninput="this.value=this.value.replace(/[^0-9()\\- ]/g,'')" required><br>
            Date Received: <input type="date" name="date_received" value="{pink_slip.date_received.isoformat() if pink_slip.date_received else ''}" required><br>
            Due Date: <input type="date" name="due_date" value="{pink_slip.due_date.isoformat() if pink_slip.due_date else ''}" required><br>
            Due Time: <input type="time" name="due_time" value="{_time_to_input_value(pink_slip.due_time)}"><br>
            Rush Fee: <input type="text" name="rush_fee" inputmode="decimal" value="{pink_slip.rush_fee:.2f}" oninput="this.value=this.value.replace(/[^0-9.]/g,'')"><br>
        </fieldset>
        <fieldset>
            <legend>Items</legend>
            <div id="items-container">{item_rows_html}
            </div>
            <br>
            <button type="button" onclick="addItem()">+ Add Another Item</button>
        </fieldset>
        <br>
        <div class="actions">
            <input type="submit" value="Save Changes">
            <button type="submit" class="danger" formaction="/delete_pink_slip/{slip_number}" formmethod="POST" formnovalidate
                onclick="return confirm('Delete slip {slip_number}? This cannot be undone.');">Delete Slip</button>
        </div>
    </form>
    <div class="actions">
        <a href="/records"><button type="button" class="secondary">Back to Records</button></a>
    </div>
    <script>
    function addItem() {{
        var container = document.getElementById('items-container');
        var row = document.createElement('div');
        row.className = 'item-row';
        row.style.marginTop = '8px';
        row.innerHTML = 'Item Type: <select name="item_type" required>{item_options_blank}</select> '
            + 'Work Description: <input type="text" name="work_description"> '
            + 'Price: <input type="text" name="price" inputmode="decimal" required> '
            + '<button type="button" onclick="this.parentElement.remove()">Remove</button>';
        var priceInput = row.querySelector('input[name="price"]');
        priceInput.addEventListener('input', function() {{
            this.value = this.value.replace(/[^0-9.]/g, '');
        }});
        container.appendChild(row);
    }}
    </script>
    """ + PAGE_END

@app.route("/export")
def export():
    slips_df = pd.read_sql_query('SELECT * FROM pink_slip', db.engine)
    items_df = pd.read_sql_query('SELECT * FROM pink_slip_item', db.engine)

    merged = items_df.merge(slips_df, left_on='slip_id', right_on='id', suffixes=('_item', '_slip'))
    export_df = merged[['slip_number', 'first_initial', 'last_name', 'phone',
                         'date_received', 'due_date', 'due_time', 'item_type',
                         'work_description', 'price', 'rush_fee', 'total_amount']]

    csv_data = export_df.to_csv(index=False)
    return Response(csv_data, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=pink_slips.csv'})

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
