// Loaded in <head> so the saved theme applies before first paint (no flash).
// "auto" (default) follows the system; "light" / "dark" force a theme.
(function () {
  var KEY = "fmt-theme";
  function read() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
  function apply(t) {
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
  }
  apply(read());
  window.fmtTheme = {
    get: read,
    set: function (t) { try { localStorage.setItem(KEY, t); } catch (e) { /* private mode */ } apply(t); },
  };
})();
