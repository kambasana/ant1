namespace MilSim.Services;

/// <summary>
/// Probabilistic combat resolution engine.
/// Replaces C&amp;C damage tables with realistic engagement modeling.
/// </summary>
public interface ICombatResolver
{
    /// <summary>
    /// Resolve a single engagement between attacker weapon and target.
    /// Returns the outcome including hits, kills, suppression, and morale effects.
    /// </summary>
    EngagementResult ResolveEngagement(EngagementContext context);

    /// <summary>
    /// Resolve all pending engagements for a tick.
    /// Handles simultaneous fire, mutual destruction, and priority targeting.
    /// </summary>
    Task<IReadOnlyList<EngagementResult>> ResolveAllEngagements(
        IReadOnlyList<EngagementContext> engagements, CancellationToken ct);

    /// <summary>Calculate probability of hit for given parameters.</summary>
    double CalculatePHit(EngagementContext context);

    /// <summary>Calculate probability of kill given a hit.</summary>
    double CalculatePKill(EngagementContext context);

    /// <summary>Calculate suppression effect on target.</summary>
    SuppressionResult CalculateSuppression(EngagementContext context);
}

public record EngagementContext(
    string AttackerUnitId,
    string TargetUnitId,
    string WeaponId,
    double Range,                  // meters
    TerrainType AttackerTerrain,
    TerrainType TargetTerrain,
    PostureType AttackerPosture,
    PostureType TargetPosture,
    bool TargetMoving,
    bool AttackerMoving,
    TrainingLevel AttackerTraining,
    TrainingLevel TargetTraining,
    double TargetSuppressionLevel, // 0-100
    WeatherType Weather,
    bool IsNight,
    bool AttackerHasNVG,
    double AttackerMorale,
    double TargetMorale
);

public record EngagementResult(
    string AttackerUnitId,
    string TargetUnitId,
    string WeaponId,
    double PHit,
    double PKill,
    bool Hit,
    DamageType DamageInflicted,
    int PersonnelCasualties,
    double SuppressionInflicted,
    double MoraleDamage,
    string NarrativeDescription    // for AAR / LLM-generated combat description
);

public enum DamageType
{
    None,
    Suppressed,
    Pinned,
    PersonnelCasualty,
    MobilityKill,
    FirepowerKill,
    CatastrophicKill,
    UnitDestroyed
}

public enum TerrainType { Open, LightVegetation, HeavyForest, Urban, Fortified, Trench }
public enum PostureType { Prone, Kneeling, Standing, Mounted }
public enum TrainingLevel { Green, Trained, Veteran, Elite }
public enum WeatherType { Clear, Rain, Fog, Snow }
