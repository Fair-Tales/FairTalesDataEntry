"""Messages for lossless character identity updates."""


class CharacterIdentityText:
    absent_snapshot = "Cannot load an absent character snapshot"
    immutable_identity = "Character analytical and storage identity are immutable"
    nonempty_name = "Character name must be nonempty"
    concurrent_change = "Character changed; reload before renaming"
    invalid_history = "Character name history is invalid; reload and review before renaming"
