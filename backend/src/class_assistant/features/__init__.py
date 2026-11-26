"""Importing the modules below registers each feature (see base.register_feature)."""
from .base import FEATURES, ChatFeature, Feature, build_features, register_feature
from . import coding_help, progress, qa, quiz, summary  # noqa: F401  (imported for registration)

__all__ = ["FEATURES", "ChatFeature", "Feature", "build_features", "register_feature"]
