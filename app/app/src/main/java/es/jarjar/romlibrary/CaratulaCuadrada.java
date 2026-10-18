package es.jarjar.romlibrary;

import android.content.Context;
import android.util.AttributeSet;
import android.widget.ImageView;

/**
 * Hueco de carátula SIEMPRE cuadrado (alto = ancho de la celda). Con
 * scaleType="fitCenter" la imagen se ve entera sea cual sea su relación de
 * aspecto (cajas de GBA apaisadas, PS1 cuadradas, Switch verticales...) --
 * mismo criterio que la galería web de /staff/romlibrary.
 */
public class CaratulaCuadrada extends ImageView {

    public CaratulaCuadrada(Context context) {
        super(context);
    }

    public CaratulaCuadrada(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    public CaratulaCuadrada(Context context, AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        super.onMeasure(widthMeasureSpec, widthMeasureSpec);
    }
}
