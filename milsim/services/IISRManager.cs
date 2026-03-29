namespace MilSim.Services;

/// <summary>
/// Intelligence, Surveillance, and Reconnaissance manager.
/// Replaces binary fog of war with graduated intel levels.
/// </summary>
public interface IISRManager
{
    /// <summary>
    /// Update the intelligence picture for a faction based on all active sensors.
    /// Called each tick during execution phase.
    /// </summary>
    Task UpdateIntelPicture(string factionId, CancellationToken ct);

    /// <summary>Get all known contacts for a faction.</summary>
    IReadOnlyList<IntelContact> GetContacts(string factionId);

    /// <summary>Get the detection level of a specific enemy unit.</summary>
    DetectionLevel GetDetectionLevel(string factionId, string targetUnitId);

    /// <summary>
    /// Attempt detection of a target by a specific sensor.
    /// Returns whether detection occurred and at what level.
    /// </summary>
    DetectionResult AttemptDetection(SensorContext context);

    /// <summary>Decay all intel contacts that haven't been re-observed.</summary>
    Task DecayIntel(string factionId, CancellationToken ct);
}

public record IntelContact(
    string ContactId,
    string FactionId,
    DetectionLevel Level,
    double LocationX,
    double LocationY,
    double LocationAccuracy,       // meters of uncertainty
    string UnitTypeEstimate,       // "Infantry", "Armor", "Unknown"
    string SizeEstimate,           // "Squad", "Platoon", "Company", "Unknown"
    int TurnLastObserved,
    string[] SourceSensors,        // which sensors contributed
    double Confidence              // 0-1 overall confidence
);

public enum DetectionLevel
{
    NoContact = 0,
    Suspected = 1,
    Possible = 2,
    Probable = 3,
    Confirmed = 4
}

public record SensorContext(
    string SensorUnitId,
    string TargetUnitId,
    SensorType SensorType,
    double Range,
    double SensorAccuracy,
    double TargetVisualSignature,
    double TargetThermalSignature,
    double TargetNoiseSignature,
    double TargetCamouflage,
    TerrainType InterveningTerrain,
    WeatherType Weather,
    bool IsNight
);

public enum SensorType
{
    Visual,
    Thermal,
    Radar,
    SIGINT,
    Acoustic,
    UAV
}

public record DetectionResult(
    bool Detected,
    DetectionLevel Level,
    double LocationAccuracy,
    string UnitTypeEstimate,
    string SizeEstimate
);
