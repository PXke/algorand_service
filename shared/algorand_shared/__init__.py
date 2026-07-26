"""Code shared by the backend API and the workers.

The two are separate deployables with their own trees and no common import
root, so anything both need used to get forked (see git history: indexnow.py
was a self-described "mirror" whose two copies differed only in how they read
config). They DO share one virtualenv on the host, so a small installed
package is the one place both can import from.

Keep this narrow: pure logic plus explicitly injected configuration. Nothing
here may import `app.*` from either service, or the fork comes back inverted.
"""
