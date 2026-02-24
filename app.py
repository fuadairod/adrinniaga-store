from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask_mail import Mail, Message
import pytz
import os

from dotenv import load_dotenv

# ---------------- CONFIG ----------------
load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True
}

if not app.config["SQLALCHEMY_DATABASE_URI"]:
    raise ValueError("DATABASE_URL not found. Check your .env file")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static/uploads/receipts")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ----------------EMAIL CONFIG ----------------
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")       # contoh: smtp.gmail.com
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587)) # default TLS
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USE_SSL"] = False
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")   # email seller
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")   # app password/email password
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_USERNAME")

mail = Mail(app)

db = SQLAlchemy(app)
MALAYSIA_TZ = pytz.timezone("Asia/Kuala_Lumpur")

@app.context_processor
def inject_datetime_malaysia():
    return dict(datetime_malaysia=datetime.now(MALAYSIA_TZ))

# ---------------- MODELS ----------------
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    description = db.Column(db.Text)
    price = db.Column(db.Float)
    image = db.Column(db.String(200))  
    stock = db.Column(db.Integer, default=0)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(200))

class OnlineOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(30), unique=True, index=True)
    customer_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    receipt = db.Column(db.String(200))
    payment_method = db.Column(db.String(50)) 
    status = db.Column(db.String(30), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OnlineOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("online_order.id"))
    product_name = db.Column(db.String(100))
    qty = db.Column(db.Integer)
    price = db.Column(db.Float)

class InventoryTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    added_stock = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship("Product", backref="inventory_transactions")


# ---------------- HELPERS ----------------
def get_cart():
    return session.get("cart", {})

def cart_total(cart):
    return sum(i["price"] * i["qty"] for i in cart.values())

def order_total(order_id):
    items = OnlineOrderItem.query.filter_by(order_id=order_id).all()
    return sum(item.price * item.qty for item in items)

@app.context_processor
def utility_processor():
    return dict(order_total=order_total)

def generate_order_no():
    today = datetime.now(MALAYSIA_TZ).strftime("%Y%m%d")
    for _ in range(5):
        last_order = OnlineOrder.query.filter(
            OnlineOrder.order_no.like(f"{today}-%")
        ).order_by(OnlineOrder.id.desc()).first()

        new_seq = int(last_order.order_no.split("-")[1]) + 1 if last_order else 1
        order_no = f"{today}-{new_seq:04d}"

        if not OnlineOrder.query.filter_by(order_no=order_no).first():
            return order_no

    raise Exception("Failed to generate unique order number")



# ---------------- STORE ----------------
@app.route("/")
def store():
    # Get values from the URL parameters
    search_query = request.args.get("search", "").strip()
    category_query = request.args.get("category", "").strip()

    # Start the query (filtering out items with 0 stock)
    query = Product.query.filter(Product.stock > 0)

    # Apply search filter if user typed something
    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    # Apply category filter if selected (Requires 'category' column in Product model)
    if category_query:
        # Note: Ensure your Product model has a 'category' column 
        # If not, you can remove this specific block
        query = query.filter(Product.description.ilike(f"%{category_query}%")) 

    products = query.all()

    return render_template(
        "store/products.html",
        products=products,
        search_query=search_query
    )


@app.route("/product/<int:pid>", methods=["GET","POST"])
def product_detail(pid):
    product = Product.query.get_or_404(pid)
    if request.method=="POST":
        cart = get_cart()
        cart[str(pid)] = {
            "name": product.name,
            "price": product.price,
            "qty": int(request.form["qty"])
        }
        session["cart"] = cart
        return redirect(url_for("cart"))
    return render_template("store/product_detail.html", product=product)

@app.route("/cart")
def cart():
    return render_template("store/cart.html", cart=get_cart(), total=cart_total(get_cart()))

@app.route("/cart/update/<int:pid>", methods=["POST"])
def update_cart(pid):
    cart = get_cart()
    pid_str = str(pid)

    if pid_str in cart:
        try:
            new_qty = int(request.form.get("qty", 1))
        except ValueError:
            new_qty = 1

        if new_qty <= 0:
            cart.pop(pid_str)
        else:
            cart[pid_str]["qty"] = new_qty

        session["cart"] = cart

    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:pid>")
def remove_from_cart(pid):
    cart = get_cart()
    cart.pop(str(pid), None)
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/cart/clear")
def clear_cart():
    session.pop("cart", None)
    return redirect(url_for("store"))

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = get_cart()

    if not cart:
        return redirect(url_for("store"))

    if request.method == "POST":
        # 1️⃣ Check stock
        for pid, item in cart.items():
            product = Product.query.get(int(pid))
            if not product or product.stock < item["qty"]:
                flash(f"Not enough stock for {item['name']}", "danger")
                return redirect(url_for("cart"))

        # 2️⃣ Save receipt
        file = request.files["receipt"]
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        # 3️⃣ Create order
        order = OnlineOrder(
            order_no=generate_order_no(),
            customer_name=request.form["name"],
            email=request.form["email"],
            phone=request.form["phone"],
            address=request.form["address"],
            payment_method=request.form["payment_method"],
            receipt=filename
        )
        db.session.add(order)
        db.session.commit()

        session["last_order_id"] = order.id

        # 4️⃣ Save order items & reduce stock
        for pid, item in cart.items():
            product = Product.query.get(int(pid))
            product.stock -= item["qty"]

            db.session.add(OnlineOrderItem(
                order_id=order.id,
                product_name=item["name"],
                qty=item["qty"],
                price=item["price"]
            ))

        db.session.commit()

        total_amount = cart_total(cart)

        # 5️⃣ Clear cart
        session.pop("cart", None)

        # 6️⃣ Send Email Notifications
        try:
            # 📨 Email to Seller
            seller_msg = Message(
                subject=f"New Order: {order.order_no}",
                recipients=[app.config["MAIL_USERNAME"]],
                body=f"""
Hai, ada order baru masuk!

Order No: {order.order_no}
Customer: {order.customer_name}
Email: {order.email}
Phone: {order.phone}
Address: {order.address}
Total: RM{total_amount:.2f}

Sila semak order dalam admin panel.
"""
            )
            mail.send(seller_msg)

            # 📨 Email to Customer
            items_list = "\n".join([
                f"- {item['name']} x{item['qty']} (RM{item['price']:.2f})"
                for item in cart.values()
            ])

            customer_msg = Message(
                subject=f"Order Confirmation - {order.order_no}",
                recipients=[order.email],
                body=f"""
Hi {order.customer_name},

Terima kasih atas pesanan anda!

Order No: {order.order_no}

Items:
{items_list}

Total Payment: RM{total_amount:.2f}

Kami akan proses order anda secepat mungkin.

Thank you for shopping with us!
"""
            )
            mail.send(customer_msg)

        except Exception as e:
            print("Email sending failed:", e)

        return redirect(url_for("success"))

    return render_template(
        "store/checkout.html",
        cart=cart,
        total=cart_total(cart)
    )




@app.route("/success")
def success():
    order_id = session.get("last_order_id")
    if not order_id:
        return redirect(url_for("store"))

    order = OnlineOrder.query.get_or_404(order_id)
    return render_template("store/success.html", order=order)


# ---------------- ADMIN ----------------
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        username = request.form["username"]
        password = request.form["password"]
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password, password):
            session["admin"] = admin.id
            return redirect(url_for("admin_orders"))
        else:
            flash("Invalid login","danger")
    return render_template("admin/login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    flash("Logged out successfully", "info")
    return redirect(url_for("store"))


@app.route("/admin/orders")
def admin_orders():
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    orders = OnlineOrder.query.all()
    return render_template("admin/orders.html", orders=orders)

@app.route("/admin/order/<int:oid>", methods=["GET","POST"])
def admin_order_detail(oid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))
        
    order = OnlineOrder.query.get_or_404(oid)
    items = OnlineOrderItem.query.filter_by(order_id=oid).all()
    
    # Calculate the total here
    total_amount = sum(item.qty * item.price for item in items)
    
    if request.method == "POST":
        order.status = request.form["status"]
        db.session.commit()
        # After a POST, we should redirect or refresh the data
        return redirect(url_for('admin_order_detail', oid=oid))
        
    return render_template("admin/order_detail.html", 
                           order=order, 
                           items=items, 
                           total_amount=total_amount) # Pass it here

@app.route("/admin/inventory")
def admin_inventory():
    if "admin" not in session:
        return redirect(url_for("admin_login"))

    transactions = InventoryTransaction.query.order_by(InventoryTransaction.created_at.desc()).all()
    return render_template("admin/inventory.html", transactions=transactions)


# ---------------- ADMIN PRODUCT MANAGEMENT ----------------
@app.route("/admin/products")
def admin_products():  # ✅ pastikan route ni ada
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    products = Product.query.all()
    return render_template("admin/products.html", products=products)

@app.route("/admin/product/add", methods=["GET","POST"])
def admin_add_product():
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    if request.method=="POST":
        name = request.form["name"]
        description = request.form["description"]
        price = float(request.form["price"])
        stock = int(request.form.get("stock",0))
        image_file = request.files.get("image")
        filename = None
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            os.makedirs(os.path.join(app.static_folder,"uploads/products"), exist_ok=True)
            image_file.save(os.path.join(app.static_folder,"uploads/products", filename))
        product = Product(name=name, description=description, price=price, image=filename, stock=stock)
        db.session.add(product)
        db.session.commit()
        flash("Product added successfully", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_form.html", action="Add", product=None)

@app.route("/admin/product/edit/<int:pid>", methods=["GET","POST"])
def admin_edit_product(pid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))

    product = Product.query.get_or_404(pid)

    if request.method == "POST":
        product.name = request.form["name"]
        product.description = request.form["description"]
        product.price = float(request.form["price"])

        stock_value = request.form.get("stock")
        if stock_value is not None and stock_value != "":
            product.stock = int(stock_value)

        image_file = request.files.get("image")
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            os.makedirs(os.path.join(app.static_folder,"uploads/products"), exist_ok=True)
            image_file.save(os.path.join(app.static_folder,"uploads/products", filename))
            product.image = filename

        db.session.commit()
        flash("Product updated successfully","success")
        return redirect(url_for("admin_products"))

    return render_template("admin/product_form.html", action="Edit", product=product)


@app.route("/admin/product/delete/<int:pid>", methods=["POST"])
def admin_delete_product(pid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    product = Product.query.get_or_404(pid)
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted successfully","success")
    return redirect(url_for("admin_products"))

@app.route("/admin/product/<int:pid>/inventory", methods=["GET", "POST"])
def admin_add_inventory(pid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))

    product = Product.query.get_or_404(pid)

    if request.method == "POST":
        added_stock = int(request.form.get("added_stock", 0))
        if added_stock > 0:
            product.stock += added_stock
            db.session.add(InventoryTransaction(
                product_id=product.id,
                added_stock=added_stock
            ))
            db.session.commit()
            flash(f"Added {added_stock} units to {product.name}", "success")
        else:
            flash("Please enter a valid stock quantity", "warning")
        return redirect(url_for("admin_inventory"))

    return render_template("admin/product_inventory.html", product=product)

# ---------------- ADMIN MANAGEMENT ----------------
@app.route("/admin/admins", methods=["GET","POST"])
def admin_manage_admins():
    if "admin" not in session:
        return redirect(url_for("admin_login"))

    admins = Admin.query.all()

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if Admin.query.filter_by(username=username).first():
            flash("Username already exists", "warning")
        else:
            new_admin = Admin(
                username=username,
                password=generate_password_hash(password)
            )
            db.session.add(new_admin)
            db.session.commit()
            flash(f"Admin '{username}' created successfully", "success")
        return redirect(url_for("admin_manage_admins"))

    return render_template("admin/manage_admins.html", admins=admins)


@app.route("/admin/admin/<int:aid>/change-password", methods=["GET","POST"])
def admin_change_password(aid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))

    admin = Admin.query.get_or_404(aid)

    if request.method == "POST":
        new_password = request.form["new_password"]
        admin.password = generate_password_hash(new_password)
        db.session.commit()
        flash(f"Password for '{admin.username}' updated successfully", "success")
        return redirect(url_for("admin_manage_admins"))

    return render_template("admin/change_password.html", admin=admin)


@app.route("/admin/order/<int:oid>/send-invoice", methods=["POST"])
def admin_send_invoice(oid):
    if "admin" not in session:
        return redirect(url_for("admin_login"))
    
    order = OnlineOrder.query.get_or_404(oid)
    items = OnlineOrderItem.query.filter_by(order_id=oid).all()
    
    # Calculate total manually if order_total helper isn't used here
    total_amount = sum(item.price * item.qty for item in items)

    try:
        # Create a professional HTML version of the invoice for the email
        msg = Message(
            subject=f"Invoice for Order #{order.order_no}",
            recipients=[order.email],
        )
        
        # We use render_template to create a clean HTML email body
        msg.html = render_template(
            "admin/email_invoice.html", 
            order=order, 
            items=items, 
            total=total_amount
        )
        
        mail.send(msg)
        flash(f"Invoice sent successfully to {order.email}", "success")
    except Exception as e:
        flash(f"Failed to send email: {str(e)}", "danger")
        
    return redirect(url_for("admin_order_detail", oid=oid))



# ---------------- CLIENT ORDER TRACKING ----------------
@app.route("/track-order", methods=["GET", "POST"])
def track_order():
    order = None
    items = []

    if request.method == "POST":
        order_no = request.form.get("order_no")

        order = OnlineOrder.query.filter_by(order_no=order_no).first()

        if not order:
            flash("Order number not found", "danger")
        else:
            items = OnlineOrderItem.query.filter_by(order_id=order.id).all()

    return render_template(
        "store/track_order.html",
        order=order,
        items=items
    )


# ---------------- INIT DATABASE ----------------
@app.before_first_request
def create_tables():
    db.create_all()

    # create default admin kalau takde
    if not Admin.query.first():
        admin = Admin(username="admin", password=generate_password_hash("admin123"))
        db.session.add(admin)
        db.session.commit()

    # create sample product kalau takde
    if not Product.query.first():
        db.session.add(Product(name="Produk A", description="Contoh A", price=10, stock=5))
        db.session.add(Product(name="Produk B", description="Contoh B", price=25, stock=2))
        db.session.commit()



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

