/**
 * Kobits 3D Particle Orb Engine (60fps Canvas / Zero-Dependency)
 * Renders distinct mathematical point-cloud orbs for autonomous agents:
 * - Axiom (Orchestrator): Concentric multi-ring gyro orb with pulsing central nucleus (#ccff00 / #facc15)
 * - Nexus (Architect): Geometric triangular / crystalline particle lattice (#38bdf8 / #ffffff)
 * - Sentinel (QA): Precision radar latitude-banded scan orb (#34d399 / #14b8a6)
 * - Aegis (Security): Ribbed meridian protective shield orb (#fbbf24 / #ef4444)
 * - Forge (Backend): Torus / Mobius continuous loop ribbon particle orb (#818cf8 / #c084fc)
 * - Pixel (UI/UX): Undulating liquid wave / morphing droplet particle orb (#f472b6 / #e879f9)
 * - Vanguard (DevOps): High-speed pulsating quantum swarm particle orb (#06b6d4 / #3b82f6)
 */

export const AGENT_ORB_CONFIGS = {
    axiom: {
        name: 'Axiom',
        title: 'Lead Autonomous Orchestrator',
        role: 'Orchestrator & Task Decomposition',
        geometry: 'gyro',
        geometryName: 'Concentric Gyro Orb',
        primaryColor: '#ccff00',
        secondaryColor: '#facc15',
        glowColor: 'rgba(204, 255, 0, 0.28)',
        description: 'Multi-ring gyroscope gimbal with central pulsing singularity',
    },
    nexus: {
        name: 'Nexus',
        title: 'Principal Systems Architect',
        role: 'Systems & Software Architect',
        geometry: 'lattice',
        geometryName: 'Triangular Crystalline Lattice',
        primaryColor: '#38bdf8',
        secondaryColor: '#ffffff',
        glowColor: 'rgba(56, 189, 248, 0.28)',
        description: 'Geodesic icosahedral lattice with crystalline interconnects',
    },
    sentinel: {
        name: 'Sentinel',
        title: 'QA & Verification Specialist',
        role: 'QA & Verification Automation',
        geometry: 'radar',
        geometryName: 'Precision Radar Scan Orb',
        primaryColor: '#34d399',
        secondaryColor: '#14b8a6',
        glowColor: 'rgba(52, 211, 153, 0.28)',
        description: 'Latitude-banded scan sphere with rotating radar sweep',
    },
    aegis: {
        name: 'Aegis',
        title: 'Chief Security & SAST Officer',
        role: 'Security & SAST Lead',
        geometry: 'meridian',
        geometryName: 'Ribbed Meridian Shield',
        primaryColor: '#fbbf24',
        secondaryColor: '#ef4444',
        glowColor: 'rgba(251, 191, 36, 0.28)',
        description: 'Shield meridian cage dome with impenetrable inner core',
    },
    forge: {
        name: 'Forge',
        title: 'Senior Full-Stack Engineer',
        role: 'Full-Stack & Core Engine Developer',
        geometry: 'torus',
        geometryName: 'Mobius Ribbon Loop',
        primaryColor: '#818cf8',
        secondaryColor: '#c084fc',
        glowColor: 'rgba(129, 140, 248, 0.28)',
        description: 'Parametric Mobius continuous loop with streaming data flow',
    },
    pixel: {
        name: 'Pixel',
        title: 'UI/UX Design Systems Specialist',
        role: 'UI/UX & Design Systems',
        geometry: 'liquid',
        geometryName: 'Morphing Liquid Wave',
        primaryColor: '#f472b6',
        secondaryColor: '#e879f9',
        glowColor: 'rgba(244, 114, 182, 0.28)',
        description: 'Undulating spherical harmonic liquid wave droplet',
    },
    vanguard: {
        name: 'Vanguard',
        title: 'Site Reliability & DevOps Engineer',
        role: 'Site Reliability & DevOps',
        geometry: 'swarm',
        geometryName: 'Quantum Swarm Orb',
        primaryColor: '#06b6d4',
        secondaryColor: '#3b82f6',
        glowColor: 'rgba(6, 182, 212, 0.28)',
        description: 'High-speed eccentric orbital swarm with velocity vectors',
    },
};

export function generateOrbParticles(geometryType, countMultiplier) {
    const mult = typeof countMultiplier === 'number' ? countMultiplier : 1.0;
    const geom = String(geometryType || 'gyro').toLowerCase();
    const R = 70;

    if (geom === 'gyro' || geom === 'axiom') {
        const particles = [];
        const coreCount = Math.round(36 * mult);
        for (let i = 0; i < coreCount; i++) {
            const u = Math.random();
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos(2 * Math.random() - 1);
            const r = (0.06 + 0.20 * Math.cbrt(u)) * R;
            particles.push({
                kind: 'core',
                baseX: r * Math.sin(phi) * Math.cos(theta),
                baseY: r * Math.sin(phi) * Math.sin(theta),
                baseZ: r * Math.cos(phi),
                color: Math.random() > 0.4 ? '#ccff00' : '#ffffff',
                size: 1.5 + Math.random() * 1.5,
                pulseOffset: Math.random() * Math.PI * 2,
            });
        }
        const rings = [
            { count: Math.round(36 * mult), radius: 0.94 * R, tiltX: 0.35, tiltZ: 0.15, speed: 0.85, color: '#ccff00' },
            { count: Math.round(30 * mult), radius: 0.74 * R, tiltX: 1.15, tiltZ: 0.75, speed: -1.15, color: '#facc15' },
            { count: Math.round(26 * mult), radius: 0.54 * R, tiltX: 0.65, tiltZ: 1.45, speed: 1.55, color: '#ccff00' },
        ];
        rings.forEach((ring, ringIdx) => {
            for (let i = 0; i < ring.count; i++) {
                const angle = (i / ring.count) * Math.PI * 2;
                particles.push({
                    kind: 'ring',
                    ringIdx: ringIdx,
                    angle: angle,
                    radius: ring.radius,
                    tiltX: ring.tiltX,
                    tiltZ: ring.tiltZ,
                    speed: ring.speed,
                    color: ring.color,
                    size: 1.3 + Math.random() * 0.8,
                });
            }
        });
        return { type: 'gyro', particles: particles, R: R };
    }

    if (geom === 'lattice' || geom === 'nexus') {
        const particles = [];
        const phi = (1 + Math.sqrt(5)) / 2;
        const baseVerts = [
            [-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0],
            [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
            [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1],
        ];
        const normFactor = R / Math.sqrt(1 + phi * phi);
        const vertices = baseVerts.map(v => [v[0] * normFactor, v[1] * normFactor, v[2] * normFactor]);

        vertices.forEach(v => {
            particles.push({
                x: v[0], y: v[1], z: v[2],
                baseX: v[0], baseY: v[1], baseZ: v[2],
                color: '#ffffff',
                size: 2.2,
                isVertex: true,
            });
        });

        for (let i = 0; i < vertices.length; i++) {
            for (let j = i + 1; j < vertices.length; j++) {
                const dx = vertices[i][0] - vertices[j][0];
                const dy = vertices[i][1] - vertices[j][1];
                const dz = vertices[i][2] - vertices[j][2];
                const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
                if (dist < 2.15 * normFactor) {
                    const midX = (vertices[i][0] + vertices[j][0]) * 0.5;
                    const midY = (vertices[i][1] + vertices[j][1]) * 0.5;
                    const midZ = (vertices[i][2] + vertices[j][2]) * 0.5;
                    const midLen = Math.sqrt(midX * midX + midY * midY + midZ * midZ);
                    const s = (R * 0.94) / (midLen || 1);
                    particles.push({
                        x: midX * s, y: midY * s, z: midZ * s,
                        baseX: midX * s, baseY: midY * s, baseZ: midZ * s,
                        color: '#38bdf8',
                        size: 1.4,
                        isVertex: false,
                    });
                }
            }
        }

        const innerCount = Math.round(20 * mult);
        for (let k = 0; k < innerCount; k++) {
            const u = Math.random();
            const theta = Math.random() * Math.PI * 2;
            const p = Math.acos(2 * Math.random() - 1);
            const r = (0.2 + 0.24 * Math.cbrt(u)) * R;
            particles.push({
                x: r * Math.sin(p) * Math.cos(theta),
                y: r * Math.sin(p) * Math.sin(theta),
                z: r * Math.cos(p),
                baseX: r * Math.sin(p) * Math.cos(theta),
                baseY: r * Math.sin(p) * Math.sin(theta),
                baseZ: r * Math.cos(p),
                color: '#38bdf8',
                size: 1.2,
                isVertex: false,
                isInner: true,
            });
        }
        return { type: 'lattice', particles: particles, R: R };
    }

    if (geom === 'radar' || geom === 'sentinel') {
        const particles = [];
        const bands = 7;
        for (let b = 0; b < bands; b++) {
            const lat = ((b + 0.5) / bands) * Math.PI - Math.PI / 2;
            const rBand = R * Math.cos(lat);
            const zBand = R * Math.sin(lat);
            const countInBand = Math.round((14 + 14 * Math.cos(lat)) * mult);
            for (let i = 0; i < countInBand; i++) {
                const lon = (i / countInBand) * Math.PI * 2;
                particles.push({
                    lon: lon,
                    lat: lat,
                    baseX: rBand * Math.cos(lon),
                    baseY: rBand * Math.sin(lon),
                    baseZ: zBand,
                    color: '#34d399',
                    size: 1.5,
                });
            }
        }
        return { type: 'radar', particles: particles, R: R };
    }

    if (geom === 'meridian' || geom === 'aegis') {
        const particles = [];
        const ribs = 8;
        const ptsPerRib = Math.round(14 * mult);
        for (let m = 0; m < ribs; m++) {
            const ribAngle = (m / ribs) * Math.PI;
            for (let j = 0; j < ptsPerRib; j++) {
                const theta = (j / Math.max(1, ptsPerRib - 1)) * Math.PI;
                const x0 = R * Math.sin(theta) * Math.cos(ribAngle);
                const y0 = R * Math.sin(theta) * Math.sin(ribAngle);
                const z0 = R * Math.cos(theta);
                particles.push({
                    kind: 'rib',
                    ribIdx: m,
                    theta: theta,
                    baseX: x0,
                    baseY: y0,
                    baseZ: z0,
                    color: '#fbbf24',
                    size: 1.4,
                });
            }
        }
        const vaultCount = Math.round(28 * mult);
        for (let k = 0; k < vaultCount; k++) {
            const u = Math.random();
            const theta = Math.random() * Math.PI * 2;
            const p = Math.acos(2 * Math.random() - 1);
            const r = (0.14 + 0.20 * Math.cbrt(u)) * R;
            particles.push({
                kind: 'vault',
                baseX: r * Math.sin(p) * Math.cos(theta),
                baseY: r * Math.sin(p) * Math.sin(theta),
                baseZ: r * Math.cos(p),
                color: '#ef4444',
                size: 1.6,
            });
        }
        return { type: 'meridian', particles: particles, R: R };
    }

    if (geom === 'torus' || geom === 'forge') {
        const particles = [];
        const count = Math.round(120 * mult);
        const Rmaj = 0.68 * R;
        const rMin = 0.30 * R;
        for (let i = 0; i < count; i++) {
            const u0 = (i / count) * Math.PI * 2;
            const strand = ((i % 3) - 1) * 0.42;
            particles.push({
                u0: u0,
                strand: strand,
                Rmaj: Rmaj,
                rMin: rMin,
                color: (i % 2 === 0) ? '#818cf8' : '#c084fc',
                size: 1.3 + Math.random() * 0.8,
            });
        }
        return { type: 'torus', particles: particles, R: R };
    }

    if (geom === 'liquid' || geom === 'pixel') {
        const particles = [];
        const count = Math.round(136 * mult);
        for (let i = 0; i < count; i++) {
            const z = 1 - (2 * (i + 0.5)) / count;
            const phi = Math.acos(Math.max(-1, Math.min(1, z)));
            const theta = i * Math.PI * (3 - Math.sqrt(5));
            particles.push({
                theta: theta,
                phi: phi,
                baseUx: Math.sin(phi) * Math.cos(theta),
                baseUy: Math.sin(phi) * Math.sin(theta),
                baseUz: Math.cos(phi),
                color: (i % 3 === 0) ? '#f472b6' : ((i % 3 === 1) ? '#e879f9' : '#fce7f3'),
                size: 1.4 + Math.random() * 0.8,
            });
        }
        return { type: 'liquid', particles: particles, R: R };
    }

    if (geom === 'swarm' || geom === 'vanguard') {
        const particles = [];
        const count = Math.round(76 * mult);
        for (let i = 0; i < count; i++) {
            const orbitR = (0.42 + 0.56 * Math.random()) * R;
            const incX = (Math.random() - 0.5) * 2.2;
            const incZ = (Math.random() - 0.5) * 2.2;
            const speed = (2.2 + Math.random() * 2.8) * (Math.random() > 0.35 ? 1 : -1);
            const initAngle = Math.random() * Math.PI * 2;
            particles.push({
                kind: 'swarm',
                orbitR: orbitR,
                incX: incX,
                incZ: incZ,
                speed: speed,
                initAngle: initAngle,
                color: (i % 2 === 0) ? '#06b6d4' : '#3b82f6',
                size: 1.3 + Math.random() * 1.0,
                prevX: 0,
                prevY: 0,
            });
        }
        const coreCount = Math.round(24 * mult);
        for (let k = 0; k < coreCount; k++) {
            const u = Math.random();
            const theta = Math.random() * Math.PI * 2;
            const p = Math.acos(2 * Math.random() - 1);
            const r = (0.05 + 0.16 * Math.cbrt(u)) * R;
            particles.push({
                kind: 'core',
                baseX: r * Math.sin(p) * Math.cos(theta),
                baseY: r * Math.sin(p) * Math.sin(theta),
                baseZ: r * Math.cos(p),
                color: '#e0f2fe',
                size: 1.5,
            });
        }
        return { type: 'swarm', particles: particles, R: R };
    }

    return generateOrbParticles('gyro', mult);
}

function rotate3D(x, y, z, rotX, rotY, rotZ) {
    const cosY = Math.cos(rotY), sinY = Math.sin(rotY);
    const x1 = x * cosY + z * sinY;
    const z1 = -x * sinY + z * cosY;

    const cosX = Math.cos(rotX), sinX = Math.sin(rotX);
    const y2 = y * cosX - z1 * sinX;
    const z2 = y * sinX + z1 * cosX;

    const cosZ = Math.cos(rotZ), sinZ = Math.sin(rotZ);
    const x3 = x1 * cosZ - y2 * sinZ;
    const y3 = x1 * sinZ + y2 * cosZ;

    return { x: x3, y: y3, z: z2 };
}

function renderGeometryScene(ctx, model, geomType, config, elapsed, rotX, rotY, rotZ, cx, cy, scale, isHovered, rippleTime) {
    const particles = model.particles;
    const R = model.R || 70;
    const geom = (geomType || 'gyro').toLowerCase();
    const D = 220; // perspective focal distance
    const hasRipple = typeof rippleTime === 'number' && rippleTime < 2.5;

    const projected = [];

    if (geom === 'gyro' || geom === 'axiom') {
        const corePulse = 1 + 0.20 * Math.sin(3.5 * elapsed);
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            let x = 0, y = 0, z = 0;
            if (p.kind === 'core') {
                const subPulse = 1 + 0.18 * Math.sin(3.5 * elapsed + (p.pulseOffset || 0));
                x = p.baseX * subPulse;
                y = p.baseY * subPulse;
                z = p.baseZ * subPulse;
            } else {
                const curAngle = p.angle + elapsed * p.speed;
                const rx0 = p.radius * Math.cos(curAngle);
                const ry0 = p.radius * Math.sin(curAngle);
                const cosTx = Math.cos(p.tiltX), sinTx = Math.sin(p.tiltX);
                const y1 = ry0 * cosTx;
                const z1 = ry0 * sinTx;
                const cosTz = Math.cos(p.tiltZ), sinTz = Math.sin(p.tiltZ);
                x = rx0 * cosTz - y1 * sinTz;
                y = rx0 * sinTz + y1 * cosTz;
                z = z1;
            }

            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 16 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }

            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }
    } else if (geom === 'lattice' || geom === 'nexus') {
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            let x = p.baseX, y = p.baseY, z = p.baseZ;
            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 14 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }
            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }

        // Draw crystalline lattice interconnects between close projected vertices
        ctx.save();
        ctx.lineWidth = 0.8;
        const thresh = 0.62 * R * scale;
        for (let i = 0; i < projected.length; i++) {
            const pi = projected[i];
            if (!pi.p.isVertex) continue;
            for (let j = i + 1; j < projected.length; j++) {
                const pj = projected[j];
                if (!pj.p.isVertex) continue;
                const dx = pi.px - pj.px;
                const dy = pi.py - pj.py;
                const d2D = Math.sqrt(dx * dx + dy * dy);
                if (d2D < thresh) {
                    const avgZ = (pi.pz + pj.pz) * 0.5;
                    const normDepth = Math.max(0.1, Math.min(1.0, (avgZ + R) / (2 * R)));
                    ctx.strokeStyle = `rgba(56, 189, 248, ${(0.18 + 0.35 * normDepth) * (1 - d2D / thresh)})`;
                    ctx.beginPath();
                    ctx.moveTo(pi.px, pi.py);
                    ctx.lineTo(pj.px, pj.py);
                    ctx.stroke();
                }
            }
        }
        ctx.restore();
    } else if (geom === 'radar' || geom === 'sentinel') {
        const sweepAngle = (2.4 * elapsed) % (Math.PI * 2);
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            let x = p.baseX, y = p.baseY, z = p.baseZ;
            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 16 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }
            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;

            const curAzimuth = (p.lon + rotY + Math.PI * 4) % (Math.PI * 2);
            let angleDiff = (curAzimuth - sweepAngle + Math.PI * 2) % (Math.PI * 2);
            let isScanned = angleDiff < 0.75;
            let scanIntensity = isScanned ? (1 - angleDiff / 0.75) : 0;

            projected.push({
                p: p,
                px: px,
                py: py,
                pz: r.z,
                persp: persp,
                isScanned: isScanned,
                scanIntensity: scanIntensity,
            });
        }
    } else if (geom === 'meridian' || geom === 'aegis') {
        const shieldWave = 1 + 0.08 * Math.sin(3.0 * elapsed);
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            let x = p.baseX, y = p.baseY, z = p.baseZ;
            if (p.kind === 'rib') {
                const wave = 1 + 0.07 * Math.sin(4 * p.theta - 3.2 * elapsed);
                x *= wave;
                y *= wave;
                z *= wave;
            } else if (p.kind === 'vault') {
                const vPulse = 1 + 0.12 * Math.sin(4 * elapsed);
                x *= vPulse;
                y *= vPulse;
                z *= vPulse;
            }

            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 15 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }

            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }
    } else if (geom === 'torus' || geom === 'forge') {
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            const u = p.u0 + 1.25 * elapsed;
            const cosHalf = Math.cos(u * 0.5);
            const sinHalf = Math.sin(u * 0.5);
            const v = p.strand * p.rMin;
            let x = (p.Rmaj + v * cosHalf) * Math.cos(u);
            let y = (p.Rmaj + v * cosHalf) * Math.sin(u);
            let z = v * sinHalf;

            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 15 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }

            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }
    } else if (geom === 'liquid' || geom === 'pixel') {
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            const rWave = R * (1 + 0.22 * Math.sin(3 * p.theta + 2.5 * elapsed) * Math.cos(2 * p.phi - 1.8 * elapsed) + 0.12 * Math.cos(4 * p.theta + 1.2 * elapsed));
            let x = rWave * p.baseUx;
            let y = rWave * p.baseUy;
            let z = rWave * p.baseUz;

            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 18 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }

            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }
    } else if (geom === 'swarm' || geom === 'vanguard') {
        for (let i = 0; i < particles.length; i++) {
            const p = particles[i];
            let x = 0, y = 0, z = 0;
            if (p.kind === 'core') {
                const cPulse = 1 + 0.22 * Math.sin(8.0 * elapsed);
                x = p.baseX * cPulse;
                y = p.baseY * cPulse;
                z = p.baseZ * cPulse;
            } else {
                const a = p.initAngle + p.speed * elapsed;
                const x0 = p.orbitR * Math.cos(a);
                const y0 = p.orbitR * Math.sin(a);
                const cosIx = Math.cos(p.incX), sinIx = Math.sin(p.incX);
                const y1 = y0 * cosIx;
                const z1 = y0 * sinIx;
                const cosIz = Math.cos(p.incZ), sinIz = Math.sin(p.incZ);
                x = x0 * cosIz - y1 * sinIz;
                y = x0 * sinIz + y1 * cosIz;
                z = z1;
            }

            if (hasRipple) {
                const d = Math.sqrt(x * x + y * y + z * z) || 1;
                const disp = 16 * Math.sin(d / 6 - rippleTime * 12) * Math.exp(-rippleTime * 2.2);
                x += (x / d) * disp;
                y += (y / d) * disp;
                z += (z / d) * disp;
            }

            const r = rotate3D(x, y, z, rotX, rotY, rotZ);
            const persp = D / (D - r.z * scale);
            const px = cx + r.x * scale * persp;
            const py = cy + r.y * scale * persp;
            projected.push({ p: p, px: px, py: py, pz: r.z, persp: persp });
        }
    }

    // Sort projected particles back to front (painter's algorithm)
    projected.sort((a, b) => a.pz - b.pz);

    // Draw particles
    for (let i = 0; i < projected.length; i++) {
        const item = projected[i];
        const p = item.p;
        const normZ = Math.max(0, Math.min(1, (item.pz + R) / (2 * R)));
        let alpha = 0.20 + 0.80 * normZ;
        let radius = (p.size || 1.4) * item.persp;
        let fill = p.color || config.primaryColor || '#ccff00';

        if (item.isScanned) {
            radius *= (1 + 1.2 * item.scanIntensity);
            alpha = Math.min(1.0, alpha + 0.6 * item.scanIntensity);
            fill = item.scanIntensity > 0.4 ? '#ffffff' : config.primaryColor;
        }

        ctx.save();
        ctx.globalAlpha = Math.max(0.12, Math.min(1.0, alpha));
        ctx.fillStyle = fill;

        // Subtle glow for foreground dots
        if (normZ > 0.65 || item.isScanned) {
            ctx.shadowColor = fill;
            ctx.shadowBlur = Math.round(5 * item.persp);
        }

        ctx.beginPath();
        ctx.arc(item.px, item.py, Math.max(0.6, radius), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }
}

export function createAgentParticleOrb(canvas, agentKey, options) {
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const opts = options || {};
    const key = String(agentKey || 'axiom').toLowerCase().replace(/^agent-/, '');
    const config = AGENT_ORB_CONFIGS[key] || AGENT_ORB_CONFIGS.axiom;
    const geomType = config.geometry || 'gyro';

    let animId = null;
    let running = true;
    let rotX = typeof opts.initialRotX === 'number' ? opts.initialRotX : 0.2;
    let rotY = typeof opts.initialRotY === 'number' ? opts.initialRotY : 0;
    let rotZ = typeof opts.initialRotZ === 'number' ? opts.initialRotZ : 0;
    let rotVelX = 0;
    let rotVelY = 0.018;
    let speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.0;
    let isHovered = false;

    const model = generateOrbParticles(geomType, opts.particleMultiplier || 0.85);
    const R = model.R || 70;

    function resize() {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const rect = canvas.getBoundingClientRect();
        const w = rect.width || canvas.width || 64;
        const h = rect.height || canvas.height || 64;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(dpr, dpr);
        return { w: w, h: h, cx: w / 2, cy: h / 2, scale: (Math.min(w, h) * 0.42) / R };
    }

    let dim = resize();

    const onEnter = () => { isHovered = true; speedMultiplier = 1.8; };
    const onLeave = () => { isHovered = false; speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.0; };
    canvas.addEventListener('mouseenter', onEnter);
    canvas.addEventListener('mouseleave', onLeave);

    const startTime = performance.now();
    let lastTime = startTime;

    function renderFrame(now) {
        if (!running) return;
        const dt = Math.min((now - lastTime) / 1000, 0.1);
        lastTime = now;
        const elapsed = (now - startTime) / 1000 * speedMultiplier;

        rotY += rotVelY * speedMultiplier * (dt * 60);
        rotX += rotVelX * (dt * 60);
        rotVelX *= 0.94;

        const w = dim.w, h = dim.h, cx = dim.cx, cy = dim.cy, scale = dim.scale;
        ctx.clearRect(0, 0, w, h);

        renderGeometryScene(ctx, model, geomType, config, elapsed, rotX, rotY, rotZ, cx, cy, scale, isHovered, 999);

        animId = requestAnimationFrame(renderFrame);
    }

    animId = requestAnimationFrame(renderFrame);

    return {
        stop: () => {
            running = false;
            if (animId) cancelAnimationFrame(animId);
            canvas.removeEventListener('mouseenter', onEnter);
            canvas.removeEventListener('mouseleave', onLeave);
        },
        setSpeed: (s) => { speedMultiplier = s; }
    };
}

export function createThinkingOrb(canvas, options) {
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const opts = options || {};
    let currentAgentKey = opts.agentKey || 'forge';
    let currentConfig = AGENT_ORB_CONFIGS[currentAgentKey] || AGENT_ORB_CONFIGS.forge;
    let model = generateOrbParticles(currentConfig.geometry, 1.35);

    let animId = null;
    let running = true;
    let rotX = 0.25;
    let rotY = 0;
    let rotZ = 0;
    let rotVelX = 0;
    let rotVelY = 0.022;
    let speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.35;
    let rippleTime = 999;

    let isDragging = false;
    let lastMouseX = 0;
    let lastMouseY = 0;
    let mouseNormX = 0;
    let mouseNormY = 0;
    let isHovered = false;

    function resize() {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const rect = canvas.getBoundingClientRect();
        const w = rect.width || canvas.width || 256;
        const h = rect.height || canvas.height || 256;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(dpr, dpr);
        return { w: w, h: h, cx: w / 2, cy: h / 2, scale: (Math.min(w, h) * 0.44) / model.R };
    }

    let dim = resize();

    const onMouseDown = (e) => {
        isDragging = true;
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
    };
    const onMouseMove = (e) => {
        const rect = canvas.getBoundingClientRect();
        mouseNormX = ((e.clientX - rect.left) - dim.cx) / dim.cx;
        mouseNormY = ((e.clientY - rect.top) - dim.cy) / dim.cy;
        if (isDragging) {
            const dx = e.clientX - lastMouseX;
            const dy = e.clientY - lastMouseY;
            rotVelY = dx * 0.008;
            rotVelX = dy * 0.008;
            rotY += rotVelY;
            rotX += rotVelX;
            lastMouseX = e.clientX;
            lastMouseY = e.clientY;
        }
    };
    const onMouseUp = () => { isDragging = false; };
    const onClick = () => {
        rippleTime = 0;
        speedMultiplier = 2.4;
        setTimeout(() => { speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.35; }, 1400);
    };
    const onMouseEnter = () => { isHovered = true; };
    const onMouseLeave = () => { isHovered = false; isDragging = false; mouseNormX = 0; mouseNormY = 0; };

    canvas.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('click', onClick);
    canvas.addEventListener('mouseenter', onMouseEnter);
    canvas.addEventListener('mouseleave', onMouseLeave);

    const startTime = performance.now();
    let lastTime = startTime;

    function renderFrame(now) {
        if (!running) return;
        const dt = Math.min((now - lastTime) / 1000, 0.1);
        lastTime = now;
        const elapsed = (now - startTime) / 1000 * speedMultiplier;
        rippleTime += dt;

        if (!isDragging) {
            rotY += (rotVelY * speedMultiplier + mouseNormX * 0.006) * (dt * 60);
            rotX += (rotVelX + mouseNormY * 0.004) * (dt * 60);
            rotVelX *= 0.95;
            rotVelY += (0.022 - rotVelY) * 0.04;
        }

        const w = dim.w, h = dim.h, cx = dim.cx, cy = dim.cy, scale = dim.scale;
        ctx.clearRect(0, 0, w, h);

        const glowGrad = ctx.createRadialGradient(cx, cy, 2, cx, cy, dim.scale * model.R * 1.15);
        glowGrad.addColorStop(0, currentConfig.glowColor || 'rgba(204, 255, 0, 0.28)');
        glowGrad.addColorStop(0.65, 'rgba(0, 0, 0, 0.04)');
        glowGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = glowGrad;
        ctx.fillRect(0, 0, w, h);

        renderGeometryScene(ctx, model, currentConfig.geometry, currentConfig, elapsed, rotX, rotY, rotZ, cx, cy, scale, isHovered, rippleTime);

        animId = requestAnimationFrame(renderFrame);
    }

    animId = requestAnimationFrame(renderFrame);

    return {
        stop: () => {
            running = false;
            if (animId) cancelAnimationFrame(animId);
            canvas.removeEventListener('mousedown', onMouseDown);
            window.removeEventListener('mousemove', onMouseMove);
            window.removeEventListener('mouseup', onMouseUp);
            canvas.removeEventListener('click', onClick);
            canvas.removeEventListener('mouseenter', onMouseEnter);
            canvas.removeEventListener('mouseleave', onMouseLeave);
        },
        setAgent: (agentKey, speedBoost) => {
            const key = String(agentKey || 'axiom').toLowerCase().replace(/^agent-/, '');
            if (AGENT_ORB_CONFIGS[key]) {
                currentAgentKey = key;
                currentConfig = AGENT_ORB_CONFIGS[key];
                model = generateOrbParticles(currentConfig.geometry, 1.35);
                dim.scale = (Math.min(dim.w, dim.h) * 0.44) / model.R;
                rippleTime = 0;
                if (speedBoost) {
                    speedMultiplier = 2.2;
                    setTimeout(() => { speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.35; }, 1200);
                }
            }
        },
        setSpeed: (s) => { speedMultiplier = s; },
        triggerPulse: () => {
            rippleTime = 0;
            speedMultiplier = 2.2;
            setTimeout(() => { speedMultiplier = typeof opts.speed === 'number' ? opts.speed : 1.35; }, 1200);
        }
    };
}
