from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


COMPONENT_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet"
PACKAGE_NAME = "ubpet_profiles_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(COMPONENT_PATH)]
sys.modules[PACKAGE_NAME] = package

spec = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.profiles",
    COMPONENT_PATH / "profiles.py",
)
profiles = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = profiles
spec.loader.exec_module(profiles)


class AccountProfileTests(unittest.TestCase):
    def test_bundled_profiles_have_distinct_app_ids_and_keys(self):
        upet = profiles.get_profile(profiles.PROFILE_UPET)
        meowant = profiles.get_profile(profiles.PROFILE_MEOWANT)

        self.assertEqual(upet.app_id, "970010026")
        self.assertEqual(meowant.app_id, "970010016")
        self.assertEqual(meowant.base_url, "https://apis.airrobo-home.com")
        self.assertEqual(meowant.product, "97001")
        self.assertEqual(len(meowant.app_key), 32)
        self.assertNotEqual(meowant.app_key, upet.app_key)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown account profile"):
            profiles.get_profile("not-a-profile")

    def test_legacy_meowant_profile_id_is_supported(self):
        legacy = profiles.get_profile(profiles.LEGACY_PROFILE_MEOWANT)

        self.assertEqual(legacy.profile_id, profiles.PROFILE_MEOWANT)


if __name__ == "__main__":
    unittest.main()
