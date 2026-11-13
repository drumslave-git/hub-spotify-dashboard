# Project rules

## Keep the README screenshots current

`docs/screenshots/*.png` show the dashboard in the README. Whenever a change
alters what the page looks like (anything in `web/` that is visible, or what
the server sends the page that shows on screen), in the same commit:

1. Run `.venv/Scripts/python.exe tools/screenshots.py` (or `python
   tools/screenshots.py`) to regenerate all of them.
2. Look at each image and check it shows the change and nothing is broken.
3. If a new view or state is worth showing (or one shown no longer exists),
   update `SHOTS` in `tools/screenshots.py` and the screenshot table near the
   top of `README.md` together, captions included.
4. Commit the PNGs with the code change.

Weather, sunrise, sunset and the time shown are fixed per shot; only the date
changes between runs, so an unchanged page gives near-identical images. Don't commit regenerated images when the page didn't change.
