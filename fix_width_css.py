
with open("static/css/style.css", "r", encoding="utf-8") as f:
    css = f.read()

# 1. Update student editor width to 85vw
css = css.replace(".exam-app .site-nav{width:65vw;", ".exam-app .site-nav{width:85vw;")
css = css.replace(".exam-shell{margin:0 auto 18px;max-width:65vw;", ".exam-shell{margin:0 auto 18px;max-width:85vw;")

# 2. Fix select and option visibility in dark/light mode
# I will append option styles at the end of the file.
css += "\n/* Fix for select options native background in dark mode */\noption { background-color: var(--bg-0); color: var(--text-0); }\n"

with open("static/css/style.css", "w", encoding="utf-8") as f:
    f.write(css)
