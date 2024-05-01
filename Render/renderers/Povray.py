# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2017 Yorik van Havre <yorik@uncreated.net>              *
# *   Copyright (c) 2022 Howetuft <howetuft-at-gmail>                       *
# *   Copyright (c) 2023 Howetuft <howetuft-at-gmail>                       *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""POV-Ray renderer plugin for FreeCAD Render workbench."""

# Suggested documentation link:
# https://www.povray.org/documentation/3.7.0/r3_0.html#r3_1

# NOTE:
# Please note that POV-Ray coordinate system appears to be different from
# FreeCAD's one (z and y permuted)
# See here: https://www.povray.org/documentation/3.7.0/t2_2.html#t2_2_1_1
#
# FreeCAD (z is up):         Povray (y is up):
#
#
#  z  y                         y  z
#  | /                          | /
#  .--x                         .--x
#
#

import os
import re
import mimetypes
import math

import FreeCAD as App

from .utils.misc import fovy_to_fovx


TEMPLATE_FILTER = "Povray templates (povray_*.pov)"

mimetypes.init()


# ===========================================================================
#                             Write functions
# ===========================================================================


def write_mesh(name, mesh, material, **kwargs):
    """Compute a string in renderer SDL to represent a FreeCAD mesh."""
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision and we replace '#' with '_'
    name = name + "_"
    name = name.replace("#", "_")

    # Material values
    materialvalues = material.get_material_values(
        name,
        _write_texture,
        _write_value,
        _write_texref,
        kwargs["project_directory"],
    )

    # Material
    material = _write_material(name, materialvalues)

    # Textures
    textures = materialvalues.write_textures()
    if textures:
        textures = f"// Textures\n{textures}"

    # Get mesh file
    povfile = mesh.write_file(name, mesh.ExportType.POVRAY)

    # Transformation
    # (see https://www.povray.org/documentation/3.7.0/r3_3.html#r3_3_1_12_4)
    transfo = mesh.transformation
    yaw, pitch, roll = transfo.get_rotation_ypr()
    scale = transfo.scale
    posx, posy, posz = transfo.get_translation()

    snippet = f"""
#include "{povfile}"
{textures}// Instance to render {name}
object {{
    {name}
    {material}
    matrix <1,0,0, 0,0,1, 0,1,0, 0,0,0>
    rotate <{-roll}, 0, 0>
    rotate <0, 0, {-pitch}>
    rotate <0, {-yaw}, 0>
    scale {scale}
    translate <{posx}, {posz}, {posy}>
}}  // {name}
"""
    return snippet


def write_camera(name, pos, updir, target, fov, resolution, **kwargs):
    """Compute a string in renderer SDL to represent a camera."""
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"
    width, height = resolution

    # Pov-ray uses an horizontal fov, so we have to convert
    fov = fovy_to_fovx(fov, *resolution)

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares camera '{name}'
camera {{
    perspective
    location  <{pos.Base.x},{pos.Base.z},{pos.Base.y}>
    right     {width / height} * x
    up        y
    look_at   <{target.x},{target.z},{target.y}>
    sky       <{updir.x},{updir.z},{updir.y}>
    angle     {fov}
}}
"""
    return snippet


def write_pointlight(name, pos, color, power, **kwargs):
    """Compute a string in renderer SDL to represent a point light."""
    # Note: power is of no use for POV-Ray, as light intensity is determined
    # by RGB (see POV-Ray documentation), therefore it is ignored.

    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"
    color = color.to_linear()
    factor = power / 100

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares point light '{name}'
light_source {{
    <{pos.x},{pos.z},{pos.y}>
    color rgb<{color[0] * factor},{color[1] * factor},{color[2] * factor}>
}}
"""

    return snippet


def write_arealight(
    name, pos, size_u, size_v, color, power, transparent, **kwargs
):
    """Compute a string in renderer SDL to represent an area light."""
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"

    # Dimensions of the point sources array
    # (area light is treated as point sources array, see POV-Ray documentation)
    size_1 = 20
    size_2 = 20

    # Prepare area light axes
    rot = pos.Rotation
    axis1 = rot.multVec(App.Vector(size_u, 0.0, 0.0))
    axis2 = rot.multVec(App.Vector(0.0, size_v, 0.0))

    # Prepare color
    color = color.to_linear()

    # Prepare shape points for 'look_like'
    points = [
        (+axis1 + axis2) / 2,
        (+axis1 - axis2) / 2,
        (-axis1 - axis2) / 2,
        (-axis1 + axis2) / 2,
        (+axis1 + axis2) / 2,
    ]
    points = [f"<{p.x},{p.z},{p.y}>" for p in points]
    points = ", ".join(points)

    factor = power / 100

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares area light {name}
#declare {name}_shape = polygon {{
    5, {points}
    texture {{ pigment{{ color rgb <{color[0]},{color[1]},{color[2]}>}}
              finish {{ ambient 1 }}
            }} // end of texture
}}
light_source {{
    <{pos.Base.x},{pos.Base.z},{pos.Base.y}>
    color rgb <{color[0] * factor},{color[1] * factor},{color[2] * factor}>
    area_light <{axis1.x},{axis1.z},{axis1.y}>,
               <{axis2.x},{axis2.z},{axis2.y}>,
               {size_1}, {size_2}
    adaptive 1
    looks_like {{ {name}_shape }}
    jitter
}}
"""
    return snippet


def write_sunskylight(
    name,
    direction,
    distance,
    turbidity,
    albedo,
    sun_intensity,
    sky_intensity,
    **kwargs,
):
    """Compute a string in renderer SDL to represent a sunsky light.

    Since POV-Ray does not provide a built-in Hosek-Wilkie feature, sunsky is
    modeled by a white parallel light, with a simple gradient skysphere.
    Please note it is a very approximate and limited model (works better for
    sun high in the sky...)
    """
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"

    location = direction.normalize()
    location.Length = distance

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares sunsky light {name}
// sky ------------------------------------
sky_sphere{{
    pigment{{ gradient y
       color_map{{
           [0.0 color rgb<1,1,1> * {sky_intensity} ]
           [0.8 color rgb<0.18,0.28,0.75> * {sky_intensity}]
           [1.0 color rgb<0.75,0.75,0.75> * {sky_intensity}]}}
           scale 2
           translate -1
    }} // end pigment
}} // end sky_sphere
// sun -----------------------------------
global_settings {{ ambient_light rgb<1, 1, 1> }}
light_source {{
    <{location.x},{location.z},{location.y}>
    color rgb <1,1,1> * {sun_intensity}
    parallel
    point_at <0,0,0>
    adaptive 1
}}
"""

    return snippet


def write_imagelight(name, image, **_):
    """Compute a string in renderer SDL to represent an image-based light."""
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"

    # Find image type
    # exr | gif | hdr | iff | jpeg | pgm | png | ppm | sys | tga | tiff
    _, ext = os.path.splitext(image)
    ext = ext.lower()
    print(ext)

    map_ext = {
        ".exr": "exr",
        ".gif": "gif",
        ".hdr": "hdr",
        ".hdri": "hdr",
        ".iff": "iff",
        ".jpeg": "jpeg",
        ".jpg": "jpeg",
        ".pgm": "pgm",
        ".png": "png",
        ".ppm": "ppm",
        ".sys": "sys",
        ".tga": "tga",
        ".tiff": "tiff",
        ".tif": "tiff",
    }

    bitmap_type = map_ext.get(ext, "")

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares image-based light {name}
// hdr environment -----------------------
sky_sphere{{
    matrix < -1, 0, 0,
              0, 1, 0,
              0, 0, 1,
              0, 0, 0 >
    pigment{{
        image_map{{ {bitmap_type} "{image}"
                   gamma 1
                   map_type 1 interpolate 2}}
    }} // end pigment
}} // end sphere with hdr image
"""

    return snippet


def write_distantlight(
    name,
    color,
    power,
    direction,
    angle,
    **kwargs,
):
    # pylint: disable=unused-argument
    """Compute a string in renderer SDL to represent a distant light."""
    # POV-Ray has a lot of reserved keywords, so we suffix name with a '_' to
    # avoid any collision
    name = name + "_"

    # Nota: angle is not supported by Povray

    factor = power / 5
    color = color.to_linear()

    snippet = f"""
// Generated by FreeCAD (http://www.freecadweb.org/)
// Declares distant light {name}
light_source {{
    <0,0,0>
    color rgb <{color[0] * factor},{color[1] * factor},{color[2] * factor}>
    parallel
    point_at <{direction.x},{direction.z},{direction.y}>
    adaptive 1
}}
"""

    return snippet


# ===========================================================================
#                              Material implementation
# ===========================================================================


def _write_material(name, matval):
    """Compute a string in the renderer SDL, to represent a material.

    This function should never fail: if the material is not recognized,
    a fallback material is provided.
    """
    shadertype = matval.shadertype
    try:
        material_function = MATERIALS[shadertype]
    except KeyError:
        msg = (
            "'{}' - Material '{}' unknown by renderer, using fallback "
            "material\n"
        )
        App.Console.PrintWarning(msg.format(name, shadertype))
        return _write_material_fallback(name, matval.default_color)

    snippet_mat = material_function(name, matval)

    return snippet_mat


def _write_material_passthrough(name, matval):
    """Compute a string in the renderer SDL for a passthrough material."""
    snippet = matval["string"]
    texture = matval.passthrough_texture
    return snippet.format(n=name, c=matval.default_color, tex=texture)


def _write_material_glass(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a glass material."""
    snippet = f"""
    texture {{
        {matval["color"]}
        finish {{
            specular 1
            roughness 0.001
            ambient 0
            diffuse 0
            reflection 0.1
            }}
        }}
    interior {{
        ior {matval["ior"]}
        caustics 1
        }}"""
    return snippet


def _write_material_disney(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a Disney material.

    Caveat: this is a very rough implementation, as the Disney shader does not
    exist at all in Pov-Ray.
    """
    # If disney.subsurface is 0, we just omit the subsurface statement,
    # as it is very slow to render
    subsurface = (
        f"""subsurface {{ translucency {matval["subsurface"]} }}"""
        if float(matval["subsurface"]) > 0
        else ""
    )

    snippet = f"""
    texture {{
        {matval["basecolor"]}
        finish {{
            diffuse albedo 0.8
            specular {matval["specular"]}
            roughness {matval["roughness"]}
            conserve_energy
            reflection {{
                {matval["specular"]}
                metallic
            }}
            {subsurface}
            irid {{ {matval["clearcoatgloss"]} }}
        }}

    }}"""
    return snippet


def _write_material_diffuse(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a Diffuse material."""
    bump = matval["bump"] if matval.has_bump() else ""
    normal = matval["normal"] if matval.has_normal() else ""
    snippet = f"""texture {{
        {matval["color"]}
        finish {{ diffuse albedo 1 }}
        {bump}
        {normal}
    }}"""
    return snippet


def _write_material_pbr(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a Diffuse material."""
    bump = matval["bump"] if matval.has_bump() else ""
    normal = matval["normal"] if matval.has_normal() else ""

    specular = (
        float(matval["specular"])
        if not matval.is_texture("specular")
        else 0.05
    )
    metallic = (
        float(matval["metallic"])
        if not matval.is_texture("metallic")
        else 0.05
    )

    if not math.isclose(metallic, 0.0) and math.isclose(specular, 0.0):
        specular = 0.2  # Non-null is required to get metallic work...

    snippet = f"""texture {{
        {matval["basecolor"]}
        {bump}
        {normal}
        finish {{
          diffuse 0.9
          reflection {{0.07}}
          specular {specular}
          metallic {metallic}
          roughness 0.05
          conserve_energy
        }}
    }}"""
    return snippet


def _write_material_mixed(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a Mixed material."""
    # Glass pigment
    submat_g = matval.getmixedsubmat("glass")
    snippet_g_tex = submat_g.write_textures() + "\n"

    # Diffuse pigment
    submat_d = matval.getmixedsubmat("diffuse")
    snippet_d_tex = submat_d.write_textures() + "\n"

    snippet = f"""texture {{
        {submat_g["color"]}
        finish {{
            phong 1
            roughness 0.001
            ambient 0
            diffuse 0
            reflection 0.1
        }}
    }}
    interior {{ior {submat_g["ior"]} caustics 1}}
    texture {{
        {submat_d["color"]}
        finish {{ diffuse 1 }}
    }}"""
    snippet = snippet_g_tex + snippet_d_tex + snippet
    snippet = snippet.replace("transparency", str(matval["transparency"]))
    return snippet


def _write_material_carpaint(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a carpaint material."""
    snippet = f""" texture {{
        {matval["basecolor"]}
        finish {{
            diffuse albedo 0.7
            phong albedo 0
            specular albedo 0.6
            roughness 0.001
            reflection {{ 0.05 }}
            irid {{ 0.5 }}
            conserve_energy
        }}
    }}"""
    return snippet


def _write_material_fallback(name, material):
    """Compute a string in the renderer SDL for a fallback material.

    Fallback material is a simple Diffuse material.
    """
    try:
        lcol = material.default_color.to_linear()
        red = float(lcol[0])
        grn = float(lcol[1])
        blu = float(lcol[2])
        assert (0 <= red <= 1) and (0 <= grn <= 1) and (0 <= blu <= 1)
    except (AttributeError, ValueError, TypeError, AssertionError):
        red, grn, blu = 1, 1, 1
    snippet = """    texture {{
        pigment {{rgb <{r}, {g}, {b}>}}
        finish {{
            diffuse albedo 1
            }}
        }}"""
    return snippet.format(n=name, r=red, g=grn, b=blu)


def _write_material_emission(name, matval):  # pylint: disable=unused-argument
    """Compute a string in the renderer SDL for a Diffuse material."""
    bump = matval["bump"] if matval.has_bump() else ""
    normal = matval["normal"] if matval.has_normal() else ""
    snippet = f"""texture {{
        {matval["color"]}
        finish {{ diffuse albedo 1 ambient 1}}
        {bump}
        {normal}
    }}"""
    return snippet


MATERIALS = {
    "Passthrough": _write_material_passthrough,
    "Glass": _write_material_glass,
    "Disney": _write_material_disney,
    "Diffuse": _write_material_diffuse,
    "Mixed": _write_material_mixed,
    "Carpaint": _write_material_carpaint,
    "Substance_PBR": _write_material_pbr,
    "Emission": _write_material_emission,
}


# ===========================================================================
#                                Textures
# ===========================================================================

IMAGE_MIMETYPES = {
    "image/bmp": "bmp",
    "image/aces": "exr",
    "image/gif": "gif",
    "image/vnd.radiance": "hdr",
    "image/jpeg": "jpeg",
    "image/x-portable-graymap": "pgm",
    "image/png": "png",
    "image/x-portable-pixmap": "ppm",
    "image/x-tga": "tga",
    "image/tiff": "tiff",
}  # Povray claims to support also iff and sys, but I don't know those formats


def _imagetype(path):
    """Compute Povray image type, for image_map.

    Type is computed with MIME.
    """
    mimetype = mimetypes.guess_type(path)
    return IMAGE_MIMETYPES.get(mimetype[0], "")


def _texname(**kwargs):
    """Compute texture name."""
    objname = kwargs["objname"]
    propname = kwargs["propname"]
    shadertype = kwargs["shadertype"]
    parent_shadertype = kwargs["parent_shadertype"]

    parent_shadertype = (
        "" if parent_shadertype is None else parent_shadertype + "_"
    )

    name = f"{objname}_{parent_shadertype}{shadertype}_{propname}"
    if len(name) > 40:
        # Povray limits identifiers to 40 characters...
        name = f"hash{str(abs(hash(name)))}"
    return name


def _write_texture(**kwargs):
    """Compute a string in renderer SDL to describe a texture.

    The texture is computed from a property of a shader (as the texture is
    always integrated into a shader). Property's data are expected as
    arguments.

    Args:
        objname -- Object name for which the texture is computed
        propvalue -- Value of the shader property

    Returns:
        the name of the texture
        the SDL string of the texture
    """
    # Retrieve parameters
    objname = kwargs["objname"]
    propname = kwargs["propname"]
    proptype = kwargs["proptype"]
    propvalue = kwargs["propvalue"]
    shadertype = kwargs["shadertype"]
    parent_shadertype = kwargs["parent_shadertype"]
    project_directory = kwargs["project_directory"]

    # Compute texture name
    texname = _texname(**kwargs)

    # Just a few property types are supported by POV-Ray...
    if proptype not in ["RGB", "RGBA", "texonly", "texscalar"]:
        # There will be a warning in write_texref
        return texname, ""

    # Compute gamma
    gamma = "srgb" if proptype == "RGB" else 1.0

    if propname in ["normal", "displacement"]:
        msg = (
            f"[Render] [Povray] [Object '{objname[:-1]}'] "
            f"[Shader '{shadertype}'] [Parameter '{propname}'] - "
            f"Warning: Povray does not support 'normal' or 'displacement' "
            f"feature -- Skipping\n"
        )
        App.Console.PrintWarning(msg)
        return texname, ""

    imagefile = os.path.relpath(propvalue.file, project_directory)

    if shadertype in ["Glass", "glass"]:
        # Glass, either standalone ('Glass') or in mixed shader ('glass')
        imgmap_suffix = "filter all 0.7 "
    elif shadertype == "diffuse" and parent_shadertype == "Mixed":
        # Diffuse of Mixed shader
        imgmap_suffix = "transmit all transparency "
    else:
        imgmap_suffix = f"gamma {gamma}"

    if propname == "bump":
        bump_size = propvalue.scalar
        texture = f"""\
normal {{
            uv_mapping
            bump_map {{
              {_imagetype(imagefile)} "{imagefile}" gamma 1.0
              bump_size {bump_size}
              use_color
            }}
            no_bump_scale
            scale {propvalue.scale}
            rotate <0.0 0.0 {propvalue.rotation}>
            translate <{propvalue.translation_u} {propvalue.translation_v}>
        }}"""
    else:
        texture = f"""\
pigment {{
            uv_mapping
            image_map {{
              {_imagetype(imagefile)} "{imagefile}" {imgmap_suffix}
            }}
            scale {propvalue.scale}
            rotate <0.0 0.0 {propvalue.rotation}>
            translate <{propvalue.translation_u} {propvalue.translation_v}>
        }}"""

    # Compute final snippet
    snippet = f"""#declare {texname} = {texture}"""

    return texname, snippet


VALSNIPPETS = {
    "RGB": "color red {val[0]}  green {val[1]}  blue {val[2]}",
    "float": "{val}",
    "node": "",
    "RGBA": "{val.r} {val.g} {val.b} {val.a}",
    "texonly": "{val}",
    "str": "{val}",
}


def _write_value(**kwargs):
    """Compute a string in renderer SDL from a shader property value.

    Args:
        proptype -- Shader property's type
        propvalue -- Shader property's value

    The result depends on the type of the value...
    """
    # Retrieve parameters
    propname = kwargs["propname"]
    proptype = kwargs["proptype"]
    propvalue = kwargs["propvalue"]
    shadertype = kwargs["shadertype"]
    parent_shadertype = kwargs["parent_shadertype"]

    # Color conversion
    if proptype == "RGB":
        propvalue = propvalue.to_linear()

    # Snippets for values
    snippet = VALSNIPPETS[proptype]
    value = snippet.format(val=propvalue)

    # Special case
    if shadertype in ["Glass", "glass"] and propname == "color":
        value += " filter 0.7"
    elif (
        shadertype == "diffuse"
        and parent_shadertype == "Mixed"
        and propname == "color"
    ):
        value += " transmit transparency"

    if proptype == "RGB":
        value = f"pigment {{ {value} }}"

    return value


def _write_texref(**kwargs):
    """Compute a string in SDL for a reference to a texture in a material."""
    # Retrieve parameters
    objname = kwargs["objname"]
    propname = kwargs["propname"]
    proptype = kwargs["proptype"]
    propvalue = kwargs["propvalue"]
    shadertype = kwargs["shadertype"]

    # Just a few property types are supported by POV-Ray...
    # For the others, warn and take fallback
    if proptype not in ["RGB", "RGBA", "texonly", "texscalar"]:
        fallback = (
            propvalue.fallback if propvalue.fallback is not None else 0.5
        )
        msg = (
            f"[Render] [Povray] [Object '{objname[:-1]}'] "
            f"[Shader '{shadertype}'] [Parameter '{propname}'] - "
            f"Warning: Povray does not support texture for "
            f"float parameters. Fallback to default value ('{fallback}').\n"
        )
        App.Console.PrintWarning(msg)
        return fallback

    # Unsupported features...
    if propname in ["normal", "displacement"]:
        return ""  # Not supported by Povray

    # Compute texture name
    texname = _texname(**kwargs)

    # Compute statement
    statement = "normal " if propname == "bump" else "pigment "

    return f"""{statement} {{ {texname} }}"""


# ===========================================================================
#                              Test function
# ===========================================================================


def test_cmdline(_):
    """Generate a command line for test.

    This function allows to test if renderer settings (path...) are correct
    """
    params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Render")
    rpath = params.GetString("PovRayPath", "")
    return [rpath, "--help"]


# ===========================================================================
#                              Render function
# ===========================================================================


def render(
    project,
    prefix,
    batch,
    input_file,
    output_file,
    width,
    height,
    spp,
    denoise,
):
    """Generate renderer command.

    Args:
        project -- The project to render
        prefix -- A prefix string for call (will be inserted before path to
            renderer)
        batch -- A boolean indicating whether to call UI (False) or console
            (True) version of renderer
        input_file -- path to input file
        output -- path to output file
        width -- Rendered image width, in pixels
        height -- Rendered image height, in pixels
        spp -- Max samples per pixel (halt condition)
        denoise -- Flag to run denoiser

    Returns:
        The command to run renderer (string)
        A path to output image file (string)
    """

    def enclose_rpath(rpath):
        """Enclose rpath in quotes, if needed."""
        if not rpath:
            return ""
        if rpath[0] == rpath[-1] == '"':
            # Already enclosed (double quotes)
            return rpath
        if rpath[0] == rpath[-1] == "'":
            # Already enclosed (simple quotes)
            return rpath
        return f'"{rpath}"'

    params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Render")

    prefix = params.GetString("Prefix", "")
    if prefix:
        prefix += " "

    rpath = params.GetString("PovRayPath", "")
    if not rpath:
        App.Console.PrintError(
            "Unable to locate renderer executable. "
            "Please set the correct path in "
            "Edit -> Preferences -> Render\n"
        )
        return None, None
    rpath = enclose_rpath(rpath)

    # Prepare command line parameters
    args = params.GetString("PovRayParameters", "")
    if args:
        args += " "
    if "+W" in args:
        args = re.sub(r"\+W[0-9]+", f"+W{width}", args)
    else:
        args += f"+W{width} "
    if "+H" in args:
        args = re.sub(r"\+H[0-9]+", f"+H{height}", args)
    else:
        args += f"+H{height} "
    args += "-D " if batch else "+D "
    if output_file:
        args += f"""+O"{output_file}" """
    if spp:
        depth = round(math.sqrt(spp))
        args += f"+AM1 +R{depth} "
    if denoise:
        wrn = (
            "[Render][Povray] WARNING - Denoiser flag will be ignored: "
            "Povray has no denoising capabilities.\n"
        )
        App.Console.PrintWarning(wrn)

    filepath = f'"{input_file}"'

    cmd = prefix + rpath + " " + args + " " + filepath

    output = (
        output_file
        if output_file
        else os.path.splitext(input_file)[0] + ".png"
    )

    return cmd, output
