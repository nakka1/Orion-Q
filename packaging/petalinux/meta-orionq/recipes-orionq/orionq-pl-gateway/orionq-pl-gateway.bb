SUMMARY = "OrionQ separately supervised external correlation gateway"
LICENSE = "Apache-2.0"
# Modules and license are provided by the headless source recipe.
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/files/common-licenses/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"
SRC_URI = "file://orionq-pl-gateway.service"
S = "${WORKDIR}"
inherit systemd features_check
REQUIRED_DISTRO_FEATURES = "systemd"
RDEPENDS:${PN} = "orionq-core"
SYSTEMD_SERVICE:${PN} = "orionq-pl-gateway.service"
SYSTEMD_AUTO_ENABLE:${PN} = "disable"
do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/orionq-pl-gateway.service ${D}${systemd_system_unitdir}/
}
FILES:${PN} = "${systemd_system_unitdir}/orionq-pl-gateway.service"
