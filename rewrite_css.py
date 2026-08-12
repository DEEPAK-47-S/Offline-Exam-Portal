
with open("static/css/style.css", "r", encoding="utf-8") as f:
    css = f.read()

# 1. Update lang-btn color to be more visible
css = css.replace(".lang-btn{ border:none; background:transparent; color:var(--text-1);", ".lang-btn{ border:1px solid var(--glass-border); background:var(--glass-fill); color:var(--text-0);")

# 2. Update widths to 65vw
css = css.replace(".exam-app .site-nav{width:90vw;max-width:none;margin:16px auto}", ".exam-app .site-nav{width:65vw;max-width:none;margin:16px auto}")
css = css.replace(".teacher-wrap .site-nav{width:90vw;max-width:none;margin:16px auto}", ".teacher-wrap .site-nav{width:65vw;max-width:none;margin:16px auto}")

# 3. Exam shell max width to 65vw and margin auto
css = css.replace(".exam-shell{margin:0 18px 18px;display:grid", ".exam-shell{margin:0 auto 18px;max-width:65vw;display:grid")

# 4. Increase min-height of work area
css = css.replace(".work{display:grid;grid-template-rows:54px 1fr 31%;min-height:0}", ".work{display:grid;grid-template-rows:54px 1fr 31%;min-height:700px}")

# 5. Add small space between elements
css = css.replace(".panel{padding:30px;display:grid;align-content:start;gap:14px}", ".panel{padding:34px;display:grid;align-content:start;gap:24px;margin-bottom:20px}")

# 6. Make blobs larger and move them more
css = css.replace("33%{ transform: translate(40px,-30px) scale(1.08); }", "33%{ transform: translate(60px,-50px) scale(1.15); }")
css = css.replace("66%{ transform: translate(-30px,20px) scale(0.95); }", "66%{ transform: translate(-50px,40px) scale(0.9); }")

with open("static/css/style.css", "w", encoding="utf-8") as f:
    f.write(css)
