
with open("static/css/style.css", "r", encoding="utf-8") as f:
    css = f.read()

css = css.replace(".exam-app .site-nav{width:85vw;", ".exam-app .site-nav{width:90vw;")
css = css.replace(".exam-shell{margin:0 auto 18px;max-width:85vw;", ".exam-shell{margin:0 auto 18px;max-width:90vw;")

with open("static/css/style.css", "w", encoding="utf-8") as f:
    f.write(css)
