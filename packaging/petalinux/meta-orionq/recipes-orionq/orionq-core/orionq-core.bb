SUMMARY = "OrionQ authenticated headless core without GUI or ML runtime"
LICENSE = "Apache-2.0"
# Generated from the existing headless profile; do not use an arbitrary desktop wheel.
require orionq-artifact.inc
LIC_FILES_CHKSUM = "file://LICENSE;md5=${ORIONQ_LICENSE_MD5}"
SRC_URI = "file://orionq-core-${PV}.tar.gz;name=core \
           file://orionq-core.service \
           file://platform-preflight.py"
SRC_URI[core.sha256sum] = "${ORIONQ_SOURCE_SHA256}"
S = "${WORKDIR}/orionq-core-${PV}"
inherit python_setuptools_build_meta systemd useradd features_check
REQUIRED_DISTRO_FEATURES = "systemd"
RDEPENDS:${PN} += "python3-modules (>= 3.10) ${ORIONQ_PYTHON_RUNTIME_DEPENDS}"
USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "--system orionq"
USERADD_PARAM:${PN} = "--system --gid orionq --home-dir /nonexistent --no-create-home --shell /sbin/nologin orionq"
SYSTEMD_SERVICE:${PN} = "orionq-core.service"
SYSTEMD_AUTO_ENABLE:${PN} = "disable"
do_install:append() {
    install -d ${D}${systemd_system_unitdir} ${D}${libexecdir}/orionq ${D}${datadir}/orionq/resources
    install -m 0644 ${WORKDIR}/orionq-core.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/platform-preflight.py ${D}${libexecdir}/orionq/
    install -m 0644 ${S}/core-artifact.json ${D}${datadir}/orionq/
    cp -R --no-preserve=ownership ${S}/resources/. ${D}${datadir}/orionq/resources/
}
FILES:${PN} += "${systemd_system_unitdir}/orionq-core.service ${libexecdir}/orionq ${datadir}/orionq"
# No config, certificate, runtime database, administrator or secret is installed.
# The full dependency upper bounds are verified again by platform-preflight.
