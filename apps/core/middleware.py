class ContentSecurityPolicyMiddleware:
    """Sets a Content-Security-Policy header on every response — see
    docs/SECURITY.md "Content-Security-Policy" for the deliberate
    'unsafe-eval'/'unsafe-inline' allowances this stack currently needs
    and what tightening it further would require.

    Hand-rolled rather than adding django-csp: the policy here is one
    fixed string, not something that needs django-csp's per-view nonce
    machinery or its many configurable directives — see CLAUDE.md
    "Before adding a dependency, check whether Django ... already
    solves the problem" (here, a single response header does).
    """

    # script-src needs 'unsafe-eval': Alpine.js evaluates `x-data`/
    # `x-show`/... expression strings via `new Function()`, which CSP's
    # script-src treats as eval regardless of where the expression
    # string itself came from. Alpine ships a separate CSP-safe build
    # (a restricted expression parser instead of Function()) that would
    # let this be dropped, at the cost of some expression syntax it
    # doesn't support — not adopted here, out of scope for a single
    # security-headers pass.
    # style-src needs 'unsafe-inline': a number of templates use plain
    # `style="..."` attributes (mostly one-off `margin:0` tweaks) rather
    # than a dedicated class. Removing this would mean auditing and
    # rewriting every one of them, or switching to a nonce-per-request
    # scheme — again out of scope here.
    # Every other directive is deliberately as tight as 'self' allows:
    # no external fonts/scripts/images/frames, no plugins, no framing
    # by another site (redundant with X_FRAME_OPTIONS, kept as
    # defense-in-depth since browsers that don't honor one may honor
    # the other), forms can only submit back to this same origin.
    POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.POLICY)
        return response
