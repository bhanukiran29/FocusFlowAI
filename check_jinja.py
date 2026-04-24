from flask import Flask, render_template_string
app = Flask(__name__)
@app.route('/jinja-test')
def jinja_test():
    return render_template_string("Jinja Rendered: {{ 7 * 7 }}")
if __name__ == "__main__":
    app.run()
