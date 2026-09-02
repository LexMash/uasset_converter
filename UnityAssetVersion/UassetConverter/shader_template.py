# -*- coding: utf-8 -*-
"""Шаблон URP-шейдера, в который backend_hlsl подставляет транспилированный код.

Вынесено отдельным модулем, чтобы не мешать огромную строку с логикой.
Все проходы написаны вручную и самодостаточны: они не тянут LitInput.hlsl и
не зависят от того, как URP называет свои внутренние свойства в конкретной
версии — так шейдер переживает обновление пакета.

Поля структуры входов подставляются по факту: если граф не спрашивал позицию
в мире, ни поля, ни интерполятора под неё не появится.
"""

SHADER_TEMPLATE = '''// {c_generated}
// {c_source}
// {c_nodes}{todo_header}
Shader "{shader_name}"
{{
    Properties
    {{
{properties}
        [HideInInspector] _Cutoff("Alpha Cutoff", Range(0, 1)) = {cutoff}
        [HideInInspector] _Cull("Cull", Float) = {cull}
    }}

    SubShader
    {{
        Tags
        {{
            "RenderType" = "{rtype}"
            "RenderPipeline" = "UniversalPipeline"
            "UniversalMaterialType" = "Lit"
            "Queue" = "{queue}"
        }}
        LOD 300

        HLSLINCLUDE
        #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

        CBUFFER_START(UnityPerMaterial)
{cbuffer}
            float _Cutoff;
            float _Cull;
        CBUFFER_END

{textures}
{helpers}

        struct SurfaceValues
        {{
            float3 albedo;
            float  metallic;
            float  smoothness;
            float3 emission;
            float3 normalTS;
            float  occlusion;
            float  alpha;
        }};

        struct SurfaceInputs
        {{
{surface_inputs}
        }};

        SurfaceValues EvaluateSurface(SurfaceInputs IN)
        {{
            SurfaceValues surface;
{body}
            surface.albedo     = {albedo};
            surface.metallic   = saturate({metallic});
            // {c_smoothness}
            surface.smoothness = saturate(1.0 - ({roughness}));
            surface.emission   = {emission};
            surface.normalTS   = {normal};
            surface.occlusion  = saturate({occlusion});
            surface.alpha      = saturate({alpha});
            return surface;
        }}
        ENDHLSL

        Pass
        {{
            Name "ForwardLit"
            Tags {{ "LightMode" = "UniversalForward" }}

            Blend {blend}
            ZWrite {zwrite}
            Cull [_Cull]

            HLSLPROGRAM
            #pragma vertex ForwardVertex
            #pragma fragment ForwardFragment
            #pragma target 3.0
{keyword_pragmas}
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN
            #pragma multi_compile _ _ADDITIONAL_LIGHTS_VERTEX _ADDITIONAL_LIGHTS
            // Forward+: дополнительные источники грузятся кластеризованно и без
            // этого кейворда просто не применяются. _CLUSTER_LIGHT_LOOP — имя в
            // URP 17 (Unity 6), _FORWARD_PLUS — то же в URP 14 (2022.3 LTS);
            // неиспользуемый вариант пайплайн не активирует.
            #pragma multi_compile _ _CLUSTER_LIGHT_LOOP
            #pragma multi_compile _ _FORWARD_PLUS
            #pragma multi_compile_fragment _ _ADDITIONAL_LIGHT_SHADOWS
            #pragma multi_compile_fragment _ _SHADOWS_SOFT
            #pragma multi_compile_fragment _ _SCREEN_SPACE_OCCLUSION
            #pragma multi_compile _ LIGHTMAP_ON
            #pragma multi_compile _ DIRLIGHTMAP_COMBINED
            #pragma multi_compile _ LIGHTMAP_SHADOW_MIXING
            #pragma multi_compile _ SHADOWS_SHADOWMASK
            #pragma multi_compile_fog
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            struct Attributes
            {{
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
                float4 tangentOS  : TANGENT;
{uv_attributes}
{lightmap_attribute}                float4 color      : COLOR;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            }};

            struct Varyings
            {{
                float4 positionCS  : SV_POSITION;
                float3 positionWS  : TEXCOORD0;
                float3 normalWS    : TEXCOORD1;
                float4 tangentWS   : TEXCOORD2;
{uv_varyings}
                float4 color       : COLOR;
                DECLARE_LIGHTMAP_OR_SH(staticLightmapUV, vertexSH, 8);
                float  fogFactor   : TEXCOORD9;
                UNITY_VERTEX_INPUT_INSTANCE_ID
                UNITY_VERTEX_OUTPUT_STEREO
            }};

            Varyings ForwardVertex(Attributes input)
            {{
                Varyings output = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_TRANSFER_INSTANCE_ID(input, output);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);

                VertexPositionInputs positions = GetVertexPositionInputs(input.positionOS.xyz);
                VertexNormalInputs normals = GetVertexNormalInputs(input.normalOS, input.tangentOS);

                output.positionCS = positions.positionCS;
                output.positionWS = positions.positionWS;
                output.normalWS   = normals.normalWS;
                output.tangentWS  = float4(normals.tangentWS, input.tangentOS.w * GetOddNegativeScale());
{uv_transfer}
                output.color = input.color;
                OUTPUT_LIGHTMAP_UV({lightmap_uv_src}, unity_LightmapST, output.staticLightmapUV);
                OUTPUT_SH(normals.normalWS, output.vertexSH);
                output.fogFactor = ComputeFogFactor(positions.positionCS.z);
                return output;
            }}

            half4 ForwardFragment(Varyings input, half facing : VFACE) : SV_Target
            {{
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);

                SurfaceInputs surfaceInputs = (SurfaceInputs)0;
{forward_fill}
                SurfaceValues surface = EvaluateSurface(surfaceInputs);
{alpha_clip}
                InputData inputData = (InputData)0;
                inputData.positionWS = input.positionWS;

                float sgn = input.tangentWS.w;
                float3 bitangent = sgn * cross(input.normalWS.xyz, input.tangentWS.xyz);
                half3x3 tangentToWorld = half3x3(input.tangentWS.xyz, bitangent, input.normalWS.xyz);
                inputData.tangentToWorld = tangentToWorld;
                inputData.normalWS = NormalizeNormalPerPixel(TransformTangentToWorld(surface.normalTS, tangentToWorld));

                inputData.viewDirectionWS = SafeNormalize(GetCameraPositionWS() - input.positionWS);
                inputData.shadowCoord = TransformWorldToShadowCoord(input.positionWS);
                inputData.fogCoord = input.fogFactor;
                inputData.vertexLighting = half3(0, 0, 0);
                inputData.bakedGI = SAMPLE_GI(input.staticLightmapUV, input.vertexSH, inputData.normalWS);
                inputData.normalizedScreenSpaceUV = GetNormalizedScreenSpaceUV(input.positionCS);
                inputData.shadowMask = SAMPLE_SHADOWMASK(input.staticLightmapUV);

                SurfaceData surfaceData = (SurfaceData)0;
                surfaceData.albedo     = surface.albedo;
                surfaceData.metallic   = surface.metallic;
                surfaceData.smoothness = surface.smoothness;
                surfaceData.normalTS   = surface.normalTS;
                surfaceData.emission   = surface.emission;
                surfaceData.occlusion  = surface.occlusion;
                surfaceData.alpha      = surface.alpha;
                surfaceData.specular   = half3(0, 0, 0);
                surfaceData.clearCoatMask = 0;
                surfaceData.clearCoatSmoothness = 0;

                half4 color = UniversalFragmentPBR(inputData, surfaceData);
                color.rgb = MixFog(color.rgb, inputData.fogCoord);
                color.a = {output_alpha};
                return color;
            }}
            ENDHLSL
        }}

        Pass
        {{
            Name "GBuffer"
            Tags {{ "LightMode" = "UniversalGBuffer" }}

            ZWrite {zwrite}
            ZTest LEqual
            Cull [_Cull]

            HLSLPROGRAM
            #pragma vertex GBufferVertex
            #pragma fragment GBufferFragment
            #pragma target 4.5
            // Deferred не поддерживается на GL — там пойдёт forward-проход.
            #pragma exclude_renderers gles3 glcore
{keyword_pragmas}
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN
            #pragma multi_compile_fragment _ _SHADOWS_SOFT
            // Обязателен для Deferred+: без него у шейдера нет варианта под
            // clustered deferred и меш остаётся чёрным.
            #pragma multi_compile _ _CLUSTER_LIGHT_LOOP
            #pragma multi_compile _ LIGHTMAP_ON
            #pragma multi_compile _ DIRLIGHTMAP_COMBINED
            #pragma multi_compile _ LIGHTMAP_SHADOW_MIXING
            #pragma multi_compile _ SHADOWS_SHADOWMASK
            #pragma multi_compile _ _MIXED_LIGHTING_SUBTRACTIVE
            #pragma multi_compile_fragment _ _GBUFFER_NORMALS_OCT
            #pragma multi_compile_fragment _ _RENDER_PASS_ENABLED
            #pragma multi_compile_fragment _ _WRITE_RENDERING_LAYERS
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/UnityGBuffer.hlsl"

            struct GBufferAttributes
            {{
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
                float4 tangentOS  : TANGENT;
{uv_attributes}
{lightmap_attribute}                float4 color      : COLOR;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            }};

            struct GBufferVaryings
            {{
                float4 positionCS  : SV_POSITION;
                float3 positionWS  : TEXCOORD0;
                float3 normalWS    : TEXCOORD1;
                float4 tangentWS   : TEXCOORD2;
{uv_varyings}
                float4 color       : COLOR;
                DECLARE_LIGHTMAP_OR_SH(staticLightmapUV, vertexSH, 8);
                UNITY_VERTEX_INPUT_INSTANCE_ID
                UNITY_VERTEX_OUTPUT_STEREO
            }};

            GBufferVaryings GBufferVertex(GBufferAttributes input)
            {{
                GBufferVaryings output = (GBufferVaryings)0;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_TRANSFER_INSTANCE_ID(input, output);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);

                VertexPositionInputs positions = GetVertexPositionInputs(input.positionOS.xyz);
                VertexNormalInputs normals = GetVertexNormalInputs(input.normalOS, input.tangentOS);

                output.positionCS = positions.positionCS;
                output.positionWS = positions.positionWS;
                output.normalWS   = normals.normalWS;
                output.tangentWS  = float4(normals.tangentWS, input.tangentOS.w * GetOddNegativeScale());
{uv_transfer}
                output.color = input.color;
                OUTPUT_LIGHTMAP_UV({lightmap_uv_src}, unity_LightmapST, output.staticLightmapUV);
                OUTPUT_SH(normals.normalWS, output.vertexSH);
                return output;
            }}

            FragmentOutput GBufferFragment(GBufferVaryings input, half facing : VFACE)
            {{
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);

                SurfaceInputs surfaceInputs = (SurfaceInputs)0;
{forward_fill}
                SurfaceValues surface = EvaluateSurface(surfaceInputs);
{alpha_clip}
                InputData inputData = (InputData)0;
                inputData.positionWS = input.positionWS;

                float sgn = input.tangentWS.w;
                float3 bitangent = sgn * cross(input.normalWS.xyz, input.tangentWS.xyz);
                half3x3 tangentToWorld = half3x3(input.tangentWS.xyz, bitangent, input.normalWS.xyz);
                inputData.tangentToWorld = tangentToWorld;
                inputData.normalWS = NormalizeNormalPerPixel(TransformTangentToWorld(surface.normalTS, tangentToWorld));

                inputData.viewDirectionWS = SafeNormalize(GetCameraPositionWS() - input.positionWS);
                inputData.shadowCoord = TransformWorldToShadowCoord(input.positionWS);
                inputData.bakedGI = SAMPLE_GI(input.staticLightmapUV, input.vertexSH, inputData.normalWS);
                inputData.normalizedScreenSpaceUV = GetNormalizedScreenSpaceUV(input.positionCS);
                inputData.shadowMask = SAMPLE_SHADOWMASK(input.staticLightmapUV);

                SurfaceData surfaceData = (SurfaceData)0;
                surfaceData.albedo     = surface.albedo;
                surfaceData.metallic   = surface.metallic;
                surfaceData.smoothness = surface.smoothness;
                surfaceData.normalTS   = surface.normalTS;
                surfaceData.emission   = surface.emission;
                surfaceData.occlusion  = surface.occlusion;
                surfaceData.alpha      = surface.alpha;
                surfaceData.specular   = half3(0, 0, 0);
                surfaceData.clearCoatMask = 0;
                surfaceData.clearCoatSmoothness = 0;

                // Deferred кладёт материал в G-буфер: BRDF + GI, свет считает
                // движок отдельным проходом (в т.ч. кластеризованно для Deferred+).
                BRDFData brdfData;
                InitializeBRDFData(surfaceData.albedo, surfaceData.metallic, surfaceData.specular,
                                   surfaceData.smoothness, surfaceData.alpha, brdfData);
                half3 gi = GlobalIllumination(brdfData, inputData.bakedGI, surfaceData.occlusion,
                                              inputData.positionWS, inputData.normalWS, inputData.viewDirectionWS);
                return BRDFDataToGbuffer(brdfData, inputData, surfaceData.smoothness,
                                         surfaceData.emission + gi, surfaceData.occlusion);
            }}
            ENDHLSL
        }}

        Pass
        {{
            Name "ShadowCaster"
            Tags {{ "LightMode" = "ShadowCaster" }}
            ZWrite On
            ZTest LEqual
            ColorMask 0
            Cull [_Cull]

            HLSLPROGRAM
            #pragma vertex ShadowVertex
            #pragma fragment ShadowFragment
            #pragma multi_compile_vertex _ _CASTING_PUNCTUAL_LIGHT_SHADOW
{keyword_pragmas}
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Shadows.hlsl"

            float3 _LightDirection;
            float3 _LightPosition;

            struct ShadowAttributes
            {{
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
{uv_attributes}
                float4 color      : COLOR;
            }};

            struct ShadowVaryings
            {{
                float4 positionCS : SV_POSITION;
{uv_varyings}
                float4 color      : COLOR;
                float3 positionWS : TEXCOORD8;
                float3 normalWS   : TEXCOORD9;
            }};

            ShadowVaryings ShadowVertex(ShadowAttributes input)
            {{
                ShadowVaryings output = (ShadowVaryings)0;
                float3 positionWS = TransformObjectToWorld(input.positionOS.xyz);
                float3 normalWS = TransformObjectToWorldNormal(input.normalOS);

                #if _CASTING_PUNCTUAL_LIGHT_SHADOW
                    float3 lightDirectionWS = normalize(_LightPosition - positionWS);
                #else
                    float3 lightDirectionWS = _LightDirection;
                #endif

                output.positionCS = TransformWorldToHClip(ApplyShadowBias(positionWS, normalWS, lightDirectionWS));
                #if UNITY_REVERSED_Z
                    output.positionCS.z = min(output.positionCS.z, UNITY_NEAR_CLIP_VALUE);
                #else
                    output.positionCS.z = max(output.positionCS.z, UNITY_NEAR_CLIP_VALUE);
                #endif
{uv_transfer}
                output.color = input.color;
                output.positionWS = positionWS;
                output.normalWS = normalWS;
                return output;
            }}

            half4 ShadowFragment(ShadowVaryings input) : SV_Target
            {{
                SurfaceInputs surfaceInputs = (SurfaceInputs)0;
{simple_fill}
                SurfaceValues surface = EvaluateSurface(surfaceInputs);
{alpha_clip}
                return 0;
            }}
            ENDHLSL
        }}

        Pass
        {{
            Name "DepthOnly"
            Tags {{ "LightMode" = "DepthOnly" }}
            ZWrite On
            ColorMask R
            Cull [_Cull]

            HLSLPROGRAM
            #pragma vertex DepthOnlyVertex
            #pragma fragment DepthOnlyFragment
{keyword_pragmas}
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            struct DepthOnlyAttributes
            {{
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
{uv_attributes}
                float4 color      : COLOR;
            }};

            struct DepthOnlyVaryings
            {{
                float4 positionCS : SV_POSITION;
{uv_varyings}
                float4 color      : COLOR;
                float3 positionWS : TEXCOORD8;
                float3 normalWS   : TEXCOORD9;
            }};

            DepthOnlyVaryings DepthOnlyVertex(DepthOnlyAttributes input)
            {{
                DepthOnlyVaryings output = (DepthOnlyVaryings)0;
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
{uv_transfer}
                output.color = input.color;
                output.positionWS = TransformObjectToWorld(input.positionOS.xyz);
                output.normalWS = TransformObjectToWorldNormal(input.normalOS);
                return output;
            }}

            half4 DepthOnlyFragment(DepthOnlyVaryings input) : SV_Target
            {{
                SurfaceInputs surfaceInputs = (SurfaceInputs)0;
{simple_fill}
                SurfaceValues surface = EvaluateSurface(surfaceInputs);
{alpha_clip}
                return 0;
            }}
            ENDHLSL
        }}

        Pass
        {{
            Name "Meta"
            Tags {{ "LightMode" = "Meta" }}
            Cull Off

            HLSLPROGRAM
            #pragma vertex MetaPassVertex
            #pragma fragment MetaPassFragment
            #pragma target 3.0
{keyword_pragmas}
            #pragma multi_compile_fragment _ EDITOR_VISUALIZATION

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/MetaInput.hlsl"

            struct MetaAttributes
            {{
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
{uv_attributes}
{meta_lightmap_attributes}                float4 color      : COLOR;
            }};

            struct MetaVaryings
            {{
                float4 positionCS : SV_POSITION;
{uv_varyings}
                float4 color      : COLOR;
                float3 positionWS : TEXCOORD10;
                float3 normalWS   : TEXCOORD11;
            #ifdef EDITOR_VISUALIZATION
                float2 vizUV      : TEXCOORD12;
                float4 lightCoord : TEXCOORD13;
            #endif
            }};

            MetaVaryings MetaPassVertex(MetaAttributes input)
            {{
                MetaVaryings output = (MetaVaryings)0;
                output.positionCS = UnityMetaVertexPosition(input.positionOS.xyz, {lightmap_uv_src}, {lightmap_dynamic_src}, unity_LightmapST, unity_DynamicLightmapST);
{uv_transfer}
                output.color = input.color;
                output.positionWS = TransformObjectToWorld(input.positionOS.xyz);
                output.normalWS = TransformObjectToWorldNormal(input.normalOS);
            #ifdef EDITOR_VISUALIZATION
                UnityEditorVizData(input.positionOS.xyz, input.uv0, {lightmap_uv_src}, {lightmap_dynamic_src}, output.vizUV, output.lightCoord);
            #endif
                return output;
            }}

            half4 MetaPassFragment(MetaVaryings input) : SV_Target
            {{
                SurfaceInputs surfaceInputs = (SurfaceInputs)0;
{simple_fill}
                SurfaceValues surface = EvaluateSurface(surfaceInputs);
{alpha_clip}
                MetaInput metaInput = (MetaInput)0;
                metaInput.Albedo     = surface.albedo;
                metaInput.Emission   = surface.emission;
            #ifdef EDITOR_VISUALIZATION
                metaInput.VizUV      = input.vizUV;
                metaInput.LightCoord = input.lightCoord;
            #endif
                return UnityMetaFragment(metaInput);
            }}
            ENDHLSL
        }}
    }}

    FallBack "Universal Render Pipeline/Lit"
}}
'''
