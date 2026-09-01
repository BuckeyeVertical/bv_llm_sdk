use bevy::prelude::*;

const EARTH_RADIUS_M: f64 = 6_371_000.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) struct GeoPoint {
    pub latitude: f64,
    pub longitude: f64,
}

impl GeoPoint {
    pub const fn new(latitude: f64, longitude: f64) -> Self {
        Self {
            latitude,
            longitude,
        }
    }
}

pub(super) const HOME: GeoPoint = GeoPoint::new(36.215_310_17, -96.009_945_45);

pub(super) const FLIGHT_BOUNDARY: [GeoPoint; 11] = [
    GeoPoint::new(36.219_314_393_547_8, -96.001_710_891_723_62),
    GeoPoint::new(36.220_387_681_622_6, -96.004_135_608_673_1),
    GeoPoint::new(36.220_803_144_020_7, -96.006_817_817_688),
    GeoPoint::new(36.220_595_413_097_5, -96.009_457_111_358_6),
    GeoPoint::new(36.219_816_417_222_3, -96.011_817_455_291_7),
    GeoPoint::new(36.218_189_156_367_6, -96.014_242_172_241_2),
    GeoPoint::new(36.214_995_125_741_5, -96.010_926_961_898_8),
    GeoPoint::new(36.212_839_730_579_7, -96.010_766_029_357_9),
    GeoPoint::new(36.212_805_105_273_3, -96.009_167_432_785_02),
    GeoPoint::new(36.210_606_366_937_3, -96.009_242_534_637_5),
    GeoPoint::new(36.210_658_306_350_6, -96.001_732_349_395_8),
];

pub(super) const SEARCH_BOUNDARY_1: [GeoPoint; 4] = [
    GeoPoint::new(36.216_341, -96.010_424),
    GeoPoint::new(36.216_75, -96.007_55),
    GeoPoint::new(36.218_054, -96.007_835),
    GeoPoint::new(36.217_645, -96.010_709),
];

pub(super) const LAP_ROUTE: [GeoPoint; 12] = [
    HOME,
    GeoPoint::new(36.213_628_92, -96.009_819_91),
    GeoPoint::new(36.213_592_47, -96.008_136_94),
    GeoPoint::new(36.211_422_92, -96.008_211_04),
    GeoPoint::new(36.211_464_30, -96.004_839_24),
    GeoPoint::new(36.216_455_53, -96.004_839_24),
    GeoPoint::new(36.218_835_78, -96.002_715_30),
    GeoPoint::new(36.219_616_82, -96.004_479_78),
    GeoPoint::new(36.219_986_23, -96.006_864_65),
    GeoPoint::new(36.219_801_37, -96.009_213_36),
    GeoPoint::new(36.219_114_47, -96.011_294_65),
    GeoPoint::new(36.218_086_22, -96.012_826_81),
];

/// Map GPS through Gazebo's local East-North-Up frame into Bevy.
pub(super) fn to_bevy(point: GeoPoint) -> Vec2 {
    let north = (point.latitude - HOME.latitude).to_radians() * EARTH_RADIUS_M;
    let east = (point.longitude - HOME.longitude).to_radians()
        * EARTH_RADIUS_M
        * HOME.latitude.to_radians().cos();

    Vec2::new(-north as f32, -east as f32)
}

pub(super) fn search_position(u: f32, v: f32) -> Vec2 {
    let southwest = to_bevy(SEARCH_BOUNDARY_1[0]);
    let southeast = to_bevy(SEARCH_BOUNDARY_1[1]);
    let northwest = to_bevy(SEARCH_BOUNDARY_1[3]);

    southwest + (southeast - southwest) * u + (northwest - southwest) * v
}

pub(super) fn contains(polygon: &[Vec2], point: Vec2) -> bool {
    let mut inside = false;
    let mut previous = polygon.len() - 1;

    for current in 0..polygon.len() {
        let a = polygon[current];
        let b = polygon[previous];
        let crosses = (a.y > point.y) != (b.y > point.y)
            && point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x;
        if crosses {
            inside = !inside;
        }
        previous = current;
    }

    inside
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn home_is_bevy_origin() {
        assert_eq!(to_bevy(HOME), Vec2::ZERO);
    }

    #[test]
    fn gps_axes_match_the_gazebo_to_bevy_basis() {
        let north = to_bevy(GeoPoint::new(HOME.latitude + 0.001, HOME.longitude));
        let east = to_bevy(GeoPoint::new(HOME.latitude, HOME.longitude + 0.001));

        assert!(north.x < 0.0 && north.y.abs() < 0.001);
        assert!(east.y < 0.0 && east.x.abs() < 0.001);
    }

    #[test]
    fn shared_suas_environment_uses_layout_home() {
        let environment = include_str!("../../../config/suas.env");
        let value = |key: &str| {
            environment
                .lines()
                .find_map(|line| line.strip_prefix(&format!("{key}=")))
                .unwrap()
                .parse::<f64>()
                .unwrap()
        };

        assert!((value("PX4_HOME_LAT") - HOME.latitude).abs() < 1e-9);
        assert!((value("PX4_HOME_LON") - HOME.longitude).abs() < 1e-9);
    }

    #[test]
    fn search_targets_are_inside_search_boundary_one() {
        let boundary = SEARCH_BOUNDARY_1.map(to_bevy);

        assert!(contains(&boundary, search_position(0.30, 0.35)));
        assert!(contains(&boundary, search_position(0.72, 0.68)));
    }

    #[test]
    fn lap_route_is_inside_flight_boundary() {
        let boundary = FLIGHT_BOUNDARY.map(to_bevy);

        for point in LAP_ROUTE.map(to_bevy) {
            assert!(contains(&boundary, point));
        }
    }
}
