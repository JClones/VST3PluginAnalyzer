#!/usr/bin/env python3
# Copyright (c) 2026 vladg/sound
# SPDX-License-Identifier: MIT
"""Interactively sign an existing macOS bundle, notarize it, and produce a release ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

from macos_bundle import PROJECT, macho_files, run


def signing_targets(bundle):
    """Return native files and code containers within bundle in deepest-first signing order."""
    native = list(macho_files(bundle))
    if not native:
        raise RuntimeError("The application contains no native code")
    targets = set(native)
    # Frameworks and nested applications must be sealed after their own executable files.
    for path in bundle.rglob("*"):
        if path.is_dir() and not path.is_symlink() and path.suffix in (".framework", ".app", ".xpc", ".bundle"):
            if any(path in binary.parents for binary in native):
                targets.add(path)
    return sorted(targets, key=lambda path: (-len(path.parts), str(path))) + [bundle]


def select_identity(requested=None):
    """Resolve requested certificate hash/name or prompt for a valid Developer ID Application identity."""
    result = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                            check=True, capture_output=True, text=True)
    identities = re.findall(r'^\s*\d+\)\s+([A-Fa-f0-9]{40})\s+"(Developer ID Application:[^"]+)"',
                            result.stdout, re.MULTILINE)
    if not identities:
        raise RuntimeError("Install a Developer ID Application certificate and its private key in Keychain first")
    if requested:
        for fingerprint, label in identities:
            if requested == label or requested.upper() == fingerprint.upper():
                return fingerprint, label
        raise RuntimeError("The requested identity is not a valid Developer ID Application identity in Keychain")
    # Numbered selection avoids ambiguity when several signing certificates are installed.
    for number, (_, label) in enumerate(identities, 1):
        print(f"{number}. {label}")
    answer = input("Signing identity number [1]: ").strip() or "1"
    if not answer.isdigit() or not 1 <= int(answer) <= len(identities):
        raise RuntimeError("Invalid signing identity number")
    return identities[int(answer) - 1]


def prepare_credentials(profile, label, configure=False):
    """Reuse profile in Keychain or prompt for Apple ID/Team ID associated with certificate label; configure forces setup."""
    if not configure:
        # Check saved credentials before changing any code signatures or uploading the application.
        result = subprocess.run(["xcrun", "notarytool", "history", "--keychain-profile", profile,
                                 "--output-format", "json"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Using saved notarization credentials: {profile}")
            return
        print("Saved notarization credentials could not be used; configure them now.")
    apple_id = input("Notarization Apple ID: ").strip()
    match = re.search(r"\(([A-Z0-9]{10})\)$", label)
    default_team = match.group(1) if match else ""
    team_id = input(f"Apple Developer Team ID [{default_team}]: ").strip() or default_team
    if not apple_id or not team_id:
        raise RuntimeError("Apple ID and Team ID are required")
    # Apple handles the secure password prompt directly. The password is never read,
    # logged, stored in project files, or supplied as a command-line argument by this script.
    print("Enter an Apple app-specific password at Apple's secure prompt; it will be saved in Keychain.")
    run(["xcrun", "notarytool", "store-credentials", profile, "--apple-id", apple_id, "--team-id", team_id])


def sign_bundle(bundle, identity):
    """Sign all code within bundle with identity and host-only entitlements, then validate the sealed app."""
    host = (bundle / "Contents/Frameworks/native/vst3-host-service").resolve()
    if not host.is_file() or not host.is_relative_to(bundle):
        raise RuntimeError("Bundled VST3 host is missing or outside the application")
    # Use explicit inside-out signing so the host entitlement never spreads to Python or Qt.
    for target in signing_targets(bundle):
        command = ["codesign", "--force", "--sign", identity, "--options", "runtime", "--timestamp"]
        if target == host:
            command.extend(["--entitlements", PROJECT / "packaging/macos/host.entitlements.plist"])
        run([*command, target])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", bundle])


def notarize_bundle(bundle, profile, output):
    """Submit bundle using Keychain profile and create output ZIP/checksum only after acceptance and stapling."""
    # Keep the submission archive separate from the final archive containing the stapled ticket.
    with tempfile.TemporaryDirectory(prefix="vst3-notarization-") as temporary:
        submission = Path(temporary) / "submission.zip"
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, submission])
        command = ["xcrun", "notarytool", "submit", submission, "--keychain-profile", profile,
                   "--wait", "--output-format", "json"]
        print("Submitting to Apple and waiting for notarization...", flush=True)
        result = subprocess.run([str(part) for part in command], capture_output=True, text=True)
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(result.stderr or result.stdout or "Notary service returned no result") from None
        submission_id = response.get("id")
        if result.returncode != 0 or response.get("status") != "Accepted":
            # Preserve rejection diagnostics outside the signed app for review and retry.
            if submission_id:
                log = output.with_suffix(".notary-log.json")
                run(["xcrun", "notarytool", "log", submission_id, "--keychain-profile", profile, log])
            raise RuntimeError(f"Notarization was not accepted: {response}")
        print(f"Accepted notarization submission: {submission_id}")
    run(["xcrun", "stapler", "staple", bundle])
    run(["xcrun", "stapler", "validate", bundle])
    run(["codesign", "--verify", "--deep", "--strict", bundle])
    run(["spctl", "--assess", "--type", "execute", "--verbose=2", bundle])
    # Replace a previous release only after the new archive has been completed successfully.
    with tempfile.TemporaryDirectory(prefix="vst3-release-", dir=output.parent) as temporary:
        archive = Path(temporary) / output.name
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, archive])
        archive.replace(output)
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n", encoding="utf-8")
    print(f"Ready for distribution: {output}")


def main():
    """Parse release options and run the interactive signing/notarization workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=PROJECT / "dist/VST3 Plugin Analyzer.app")
    parser.add_argument("--identity", help="Developer ID Application name or certificate fingerprint; otherwise choose interactively")
    parser.add_argument("--profile", help="Saved notarization Keychain profile; otherwise prompt")
    parser.add_argument("--configure-credentials", action="store_true", help="Prompt for new Apple ID/Team ID and Apple's password prompt")
    parser.add_argument("--sign-only", action="store_true", help="Sign the app without submitting it to Apple")
    parser.add_argument("--plan", action="store_true", help="List signing targets without signing, credentials or network access")
    args = parser.parse_args()
    try:
        if sys.platform != "darwin":
            raise RuntimeError("Signing and notarization require macOS")
        bundle = args.bundle.resolve()
        if bundle.suffix != ".app" or not (bundle / "Contents/Info.plist").is_file():
            raise RuntimeError("Build the application bundle first with ./scripts/build-macos.sh")
        if args.plan:
            for target in signing_targets(bundle):
                print(target)
            return
        # Discover the version before changing signatures; no project packages are required.
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            version = plistlib.load(stream).get("CFBundleShortVersionString", "0.1.0")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", version):
            raise RuntimeError("The bundle version cannot be used in a release filename")
        identity, label = select_identity(args.identity)
        profile = args.profile
        if not args.sign_only:
            profile = profile or input("Notarization Keychain profile [VST3Analyzer]: ").strip() or "VST3Analyzer"
            prepare_credentials(profile, label, args.configure_credentials)
        sign_bundle(bundle, identity)
        if args.sign_only:
            print(f"Signed application: {bundle}")
            return
        output = bundle.parent / f"VST3-Plugin-Analyzer-{version}-macOS-universal2.zip"
        notarize_bundle(bundle, profile, output)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release failed: {error}\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "\nRelease cancelled.\n")


if __name__ == "__main__":
    main()
